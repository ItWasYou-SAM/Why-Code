"""Exporter contracts: normalized JSONL, high-signal selection, and the
ingestion report. Every line validates as its own record so a corrupt
export poisons nothing downstream silently."""

import json

from fakes import DEEP, REPO, TRIVIAL
from test_store import make_record

from app.domain.record_types import RecordType


def records_for(store_path):
    from app.ingestion.store import EvidenceStore

    store = EvidenceStore(store_path)
    store.begin_pr(REPO, DEEP)
    store.buffer_record(
        make_record(record_id="httpx:pr:1131", record_type=RecordType.PULL_REQUEST, pr_number=DEEP)
    )
    store.buffer_record(
        make_record(
            record_id="httpx:review_comment:456", record_type=RecordType.REVIEW_COMMENT,
                pr_number=DEEP
        )
    )
    store.buffer_record(
        make_record(record_id="httpx:commit:abc", record_type=RecordType.COMMIT, pr_number=DEEP)
    )
    store.commit_pr(REPO, DEEP)
    store.set_pr_state(REPO, 1132, "error", error="poisoned")
    return store


class TestNormalizedExport:
    def test_export_is_jsonl_and_roundtrips_through_the_model(self, tmp_path) -> None:
        from app.ingestion.exporter import export_normalized

        store = records_for(tmp_path / "evidence.db")
        out = tmp_path / "normalized.jsonl"
        export_normalized(store, out)
        lines = out.read_text().splitlines()
        assert len(lines) == 3
        ids = []
        for line in lines:
            parsed = json.loads(line)
            ids.append(parsed["record_id"])
        assert ids == ["httpx:commit:abc", "httpx:pr:1131", "httpx:review_comment:456"]

    def test_export_contains_all_required_record_types_when_fed_them(self, tmp_path) -> None:
        from app.ingestion.exporter import export_normalized

        store = records_for(tmp_path / "evidence.db")
        out = tmp_path / "normalized.jsonl"
        export_normalized(store, out)
        seen = {json.loads(line)["record_type"] for line in out.read_text().splitlines()}
        assert {"pull_request", "commit", "review_comment"} <= seen


class TestHighSignalExport:
    def test_high_signal_export_lists_pr_numbers_only(self, tmp_path) -> None:
        from app.ingestion.exporter import export_high_signal

        store = records_for(tmp_path / "evidence.db")
        store.set_pr_state(REPO, TRIVIAL, "complete")
        scores = {DEEP: {"score": 0.8, "band": "high"}, TRIVIAL: {"score": 0.1, "band": "low"}}
        out = tmp_path / "high_signal_prs.jsonl"
        export_high_signal(store, out, scores)
        rows = [json.loads(line) for line in out.read_text().splitlines()]
        assert [r["pr_number"] for r in rows] == [DEEP]
        assert "band" in rows[0] and "score" in rows[0]


class TestIngestionReport:
    def test_report_captures_good_runs(self, tmp_path) -> None:
        from app.ingestion.exporter import build_report

        store = records_for(tmp_path / "evidence.db")
        report = build_report(store, repo=REPO, api_calls=42, started_at="2026-10-19T09:00:00Z")
        assert report["repo"] == REPO
        assert report["api_calls"] == 42
        assert report["prs_total"] >= 1
        assert report["prs_errored"] == 1
        assert report["records_total"] == 3
        assert report["by_type"]["commit"] == 1

    def test_report_captures_partial_runs(self, tmp_path) -> None:
        from app.ingestion.exporter import build_report

        store = records_for(tmp_path / "evidence.db")
        report = build_report(store, repo=REPO, api_calls=7, started_at="x")
        assert report["prs_complete"] >= 1
        assert report["prs_errored"] == 1
