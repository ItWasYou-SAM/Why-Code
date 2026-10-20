"""The plan's 12 acceptance tests, written before trusting the build.

Fixtures are recorded/canned GitHub payloads plus a mocked wire (respx).
No test in this file touches the live GitHub API.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
import respx
from fakes import (
    DEEP,
    POISON,
    REPO,
    TRIVIAL,
    FakeGitHubClient,
    all_prs,
    deep_pr,
    file_payload,
    poison_pr,
    pr_payload,
    review_comment_payload,
    trivial_pr,
)

from app.domain.record_types import RecordType
from app.ingestion.exporter import (
    build_report,
    export_high_signal,
    export_normalized,
)
from app.ingestion.normalizer import normalize_pull_request, normalize_review_comment
from app.ingestion.service import IngestionService
from app.ingestion.store import EvidenceStore

BASE = f"https://api.github.com/repos/{REPO}"


def service(store: EvidenceStore, github, *, since: str = "2024-01-01", limit: int = 100):
    return IngestionService(github=github, store=store, repo=REPO, since=since, limit=limit)


def report_for(store: EvidenceStore, result) -> dict:
    return build_report(
        store,
        repo=REPO,
        api_calls=result.api_calls,
        started_at=result.started_at,
        finished_at=result.finished_at,
        run_id=result.run_id,
        selected_prs=len(result.processed),
        processed_prs=len(result.completed),
        failed_prs=len(result.errored),
        high_signal_prs=result.high_signal_prs,
        rate_limit_remaining=result.rate_limit_remaining,
        failures=result.failures,
    )


# 1 -------------------------------------------------------------------------
def test_pr_record_id_stability() -> None:
    payload = pr_payload(1131, "2024-04-02T06:30:45Z", "Fix retries")
    first = normalize_pull_request(REPO, payload)
    second = normalize_pull_request(REPO, dict(payload))
    third = normalize_pull_request(REPO, pr_payload(1132, "2024-04-02T06:30:45Z", "Other"))

    assert first.record_id == second.record_id == "httpx:pr:1131"
    assert first.content_hash == second.content_hash
    assert third.record_id != first.record_id


# 2 -------------------------------------------------------------------------
def test_merged_filter(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    closed_unmerged = trivial_pr()
    closed_unmerged["merged"] = False
    closed_unmerged["merged_at"] = None

    result = service(store, FakeGitHubClient([closed_unmerged])).run()

    assert result.processed == []
    assert store.count_records() == 0
    assert store.pr_numbers_with_status("complete") == []
    store.close()


# 3 -------------------------------------------------------------------------
def test_date_window(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    early = deep_pr()
    early["merged_at"] = "2024-03-31T23:59:59Z"
    on_boundary = trivial_pr()
    on_boundary["merged_at"] = "2024-04-01T00:00:00Z"

    service(store, FakeGitHubClient([early, on_boundary]), since="2024-04-01").run()

    assert store.get_pr_state(REPO, DEEP) == "pending"
    assert store.get_pr_state(REPO, TRIVIAL) == "complete"
    assert store.count_records() > 0
    store.close()


# 4 -------------------------------------------------------------------------
def test_review_comment_anchor(tmp_path) -> None:
    comment = review_comment_payload(456, "httpx/_client.py", 243, 240, "RIGHT", "why?")
    record = normalize_review_comment(REPO, 1131, comment)
    assert (record.payload["path"], record.payload["line"]) == ("httpx/_client.py", 243)
    assert record.payload["start_line"] == 240
    assert record.payload["side"] == "RIGHT"

    store = EvidenceStore(tmp_path / "e.db")
    service(store, FakeGitHubClient([deep_pr()])).run()
    stored = store.get_record("httpx:review_comment:456")
    assert stored is not None
    assert stored.payload["path"] == "httpx/_client.py"
    assert stored.payload["line"] == 243
    assert stored.payload["start_line"] == 240
    assert stored.payload["side"] == "RIGHT"
    store.close()


# 5 -------------------------------------------------------------------------
def test_renamed_file() -> None:
    from app.ingestion.normalizer import normalize_changed_file

    record = normalize_changed_file(
        REPO,
        1131,
        file_payload(
            "httpx/_transports/default.py",
            status="renamed",
            patch="@@ -1 +1 @@",
        )
        | {"previous_filename": "httpx/_transports.py"},
    )
    assert record.payload["status"] == "renamed"
    assert record.payload["previous_filename"] == "httpx/_transports.py"

    plain = normalize_changed_file(REPO, 1131, file_payload("httpx/_models.py"))
    assert "previous_filename" in plain.payload
    assert plain.payload["previous_filename"] is None


# 6 -------------------------------------------------------------------------
@respx.mock
def test_pagination_next_link() -> None:
    from app.ingestion.github_client import GitHubRestClient

    page_two = f"{BASE}/pulls?per_page=100&page=2"
    hit_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hit_urls.append(str(request.url))
        if len(hit_urls) == 1:
            return httpx.Response(
                200,
                json=[{"number": 1, "merged": True, "merged_at": "2024-04-01T00:00:00Z"}],
                headers={"Link": f'<{page_two}>; rel="next"'},
            )
        return httpx.Response(
            200,
            json=[{"number": 2, "merged": True, "merged_at": "2024-04-02T00:00:00Z"}],
        )

    respx.get(url__regex=rf"{re.escape(BASE)}/pulls.*").mock(side_effect=handler)
    client = GitHubRestClient(github_token="gh_test", sleeper=lambda _: None)
    try:
        got = list(client.merged_pull_requests(REPO, since="2024-01-01"))
    finally:
        client.close()

    assert [pr["number"] for pr in got] == [1, 2]
    assert len(hit_urls) == 2
    assert hit_urls[1] == page_two, "must follow Link rel=next, not guess a page number"
    assert client.total_calls() == 2


# 7 -------------------------------------------------------------------------
@respx.mock
def test_rate_limit_backoff() -> None:
    from app.ingestion.github_client import GitHubRestClient

    sleeps: list[float] = []
    responses = iter(
        [
            httpx.Response(
                429,
                headers={"Retry-After": "2", "X-RateLimit-Remaining": "0"},
                json={"message": "API rate limit exceeded"},
            ),
            httpx.Response(
                200,
                headers={"X-RateLimit-Remaining": "3981"},
                json=[{"number": 1, "merged": True, "merged_at": "2024-04-01T00:00:00Z"}],
            ),
        ]
    )
    respx.get(url__regex=rf"{re.escape(BASE)}/pulls.*").mock(
        side_effect=lambda request: next(responses)
    )
    client = GitHubRestClient(github_token="gh_test", sleeper=sleeps.append)
    try:
        got = list(client.merged_pull_requests(REPO, since="2024-01-01"))
    finally:
        client.close()

    assert [pr["number"] for pr in got] == [1]
    assert sleeps == [2.0], "Retry-After must win over the default schedule"
    assert client.rate_limit_remaining == 3981
    assert client.total_calls() == 2


# 8 -------------------------------------------------------------------------
def test_idempotent_upsert(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    service(store, FakeGitHubClient(all_prs())).run()
    first_count = store.count_records()
    first_raw = len(store.raw_ids())

    service(store, FakeGitHubClient(all_prs()), limit=100).run()
    service(store, FakeGitHubClient(all_prs())).run()

    assert store.count_records() == first_count
    assert len(store.raw_ids()) == first_raw
    duplicate_ids = store.conn.execute(
        "SELECT record_id, COUNT(*) c FROM evidence_records GROUP BY record_id HAVING c > 1"
    ).fetchall()
    assert duplicate_ids == []
    store.close()


# 9 -------------------------------------------------------------------------
def test_content_change_updates_record(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    service(store, FakeGitHubClient([deep_pr()])).run()
    before = store.get_record("httpx:pr:1131")
    assert before is not None

    changed = deep_pr()
    changed["body"] = "Revised rationale: only idempotent requests may retry."
    service(store, FakeGitHubClient([changed])).run(force=True)

    after = store.get_record("httpx:pr:1131")
    assert after is not None
    assert after.payload["body"] == "Revised rationale: only idempotent requests may retry."
    assert after.content_hash != before.content_hash
    pr_records = store.conn.execute(
        "SELECT COUNT(*) c FROM evidence_records WHERE record_id = 'httpx:pr:1131'"
    ).fetchone()
    assert pr_records["c"] == 1
    store.close()


# 10 ------------------------------------------------------------------------
def test_malformed_comment(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    result = service(store, FakeGitHubClient([deep_pr(), poison_pr()])).run()

    assert POISON in result.errored
    assert DEEP in result.completed
    assert store.get_pr_state(REPO, POISON) == "error"
    assert "numeric id" in (store.get_pr_error(REPO, POISON) or "")
    assert store.records_for_pr(REPO, POISON) == []
    assert store.get_record("httpx:pr:1131") is not None
    failure = next(f for f in result.failures if f["pr_number"] == POISON)
    assert failure["error_type"] == "InvalidRecordError"
    store.close()


# 11 ------------------------------------------------------------------------
def test_export_record_types(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    result = service(store, FakeGitHubClient([deep_pr()])).run()
    normalized = tmp_path / "normalized.jsonl"
    high_signal = tmp_path / "high_signal_prs.jsonl"
    export_normalized(store, normalized)
    export_high_signal(store, high_signal, result.scored_payload())

    rows = [json.loads(line) for line in normalized.read_text().splitlines()]
    assert {row["record_type"] for row in rows} == {rt.value for rt in RecordType}
    for row in rows:
        assert {"record_id", "record_type", "repo", "payload", "content_hash"} <= row.keys()
        assert isinstance(row["payload"], dict)

    signal_rows = [json.loads(line) for line in high_signal.read_text().splitlines()]
    assert [row["pr_number"] for row in signal_rows] == [DEEP]
    assert signal_rows[0]["band"] == "high"
    store.close()


# 12 ------------------------------------------------------------------------
def test_report_counts(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    result = service(store, FakeGitHubClient(all_prs())).run()
    report = report_for(store, result)

    counts = store.counts_by_type()
    for record_type in RecordType:
        assert report["records"][record_type.value] == counts.get(record_type.value, 0)
    assert report["records_total"] == store.count_records()
    assert report["records_total"] == sum(report["records"].values())

    assert report["run_id"].startswith("run_")
    assert re.fullmatch(r"run_\d{4}_\d{2}_\d{2}_\d{3}", report["run_id"])
    assert report["status"] == "completed_with_failures"
    assert (report["selected_prs"], report["processed_prs"], report["failed_prs"]) == (3, 2, 1)
    assert report["high_signal_prs"] == result.high_signal_prs == 1
    assert report["api_requests"] == result.api_calls > 0
    assert report["failures"][0]["pr_number"] == POISON
    assert report["failures"][0]["endpoint"] == f"pulls/{POISON}/comments"
    store.close()


# -- acceptance checklist items the 12-test plan did not name ---------------
def test_raw_responses_are_saved_locally(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    service(store, FakeGitHubClient([deep_pr()])).run()

    expected = {
        f"{REPO}:pulls/1131",
        f"{REPO}:pulls/1131/files",
        f"{REPO}:pulls/1131/commits",
        f"{REPO}:pulls/1131/reviews",
        f"{REPO}:pulls/1131/comments",
        f"{REPO}:issues/1131/comments",
        f"{REPO}:issues/108",
        f"{REPO}:git/blobs/{'f' * 40}",
    }
    assert expected <= set(store.raw_ids())
    raw = store.get_raw(f"{REPO}:pulls/1131")
    assert raw is not None and raw["number"] == DEEP
    store.close()


def test_show_command_prints_one_prs_evidence_trail(tmp_path, capsys) -> None:
    from app.ingestion import cli

    store = EvidenceStore(tmp_path / "evidence.db")
    service(store, FakeGitHubClient([deep_pr()])).run()
    store.close()

    code = cli.main(
        ["show", "--repo", REPO, "--pr", str(DEEP), "--artifacts-dir", str(tmp_path)]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "httpx:pr:1131" in out
    assert "httpx:review_comment:456" in out
    assert f"{REPO}:issues/108" in out
    assert f"{REPO}:git/blobs/{'f' * 40}" in out
    assert "raw payloads" in out

    code = cli.main(
        ["show", "--repo", REPO, "--pr", str(DEEP), "--artifacts-dir", str(tmp_path),
         "--json"]
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "complete"
    assert {row["record_type"] for row in payload["records"]} == {
        rt.value for rt in RecordType
    }
    assert code == 0


def test_report_failure_names_the_endpoint(tmp_path) -> None:
    from app.core.errors import TransientUpstreamError

    class FlakyFiles(FakeGitHubClient):
        def pr_files(self, repo, number):
            if number == TRIVIAL:
                raise TransientUpstreamError(
                    "502 after 5 retries",
                    component="ingestion",
                    endpoint=f"pulls/{number}/files",
                )
            return super().pr_files(repo, number)

    store = EvidenceStore(tmp_path / "e.db")
    result = service(store, FlakyFiles(all_prs())).run()
    report = report_for(store, result)
    failure = next(f for f in report["failures"] if f["pr_number"] == TRIVIAL)
    assert failure["endpoint"] == "pulls/1133/files"
    assert failure["message"] == "TransientUpstreamError: 502 after 5 retries"
    store.close()


def test_retry_errored_only_refetches_failed_prs(tmp_path) -> None:
    from app.core.errors import TransientUpstreamError

    class Flaky(FakeGitHubClient):
        fail_trivial = True

        def pr_files(self, repo, number):
            if number == TRIVIAL and self.fail_trivial:
                raise TransientUpstreamError(
                    "502", component="ingestion", endpoint=f"pulls/{number}/files"
                )
            return super().pr_files(repo, number)

    store = EvidenceStore(tmp_path / "e.db")
    broken = Flaky(all_prs())
    first = service(store, broken).run()
    assert TRIVIAL in first.errored
    assert DEEP in first.completed

    healthy = Flaky(all_prs())
    healthy.fail_trivial = False
    retry = IngestionService(
        github=healthy, store=store, repo=REPO, since="1970-01-01", limit=100
    ).retry_errored()

    # Both errored PRs are retried; the genuinely malformed one stays bad.
    assert retry.processed == [POISON, TRIVIAL]
    assert retry.completed == [TRIVIAL]
    assert retry.errored == [POISON]
    assert healthy.calls_for_pr(DEEP) == 0, "retry must not touch completed PRs"
    assert store.get_pr_state(REPO, TRIVIAL) == "complete"
    store.close()


@pytest.mark.parametrize("pr_number", [DEEP])
def test_run_id_is_monotonic(tmp_path, pr_number) -> None:
    store = EvidenceStore(tmp_path / "e.db")
    first = service(store, FakeGitHubClient([deep_pr()])).run()
    second = service(store, FakeGitHubClient([trivial_pr()])).run()
    assert first.run_id != second.run_id
    assert first.run_id.endswith("001")
    assert second.run_id.endswith("002")
    store.close()
