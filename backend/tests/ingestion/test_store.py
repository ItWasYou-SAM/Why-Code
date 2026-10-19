"""EvidenceStore contracts: SQLite-backed, idempotent upserts, raw
payloads kept separately from normalized records, PR state tracked for
resume and skip-if-complete, per-PR transactionality (D-0007)."""

import pytest
from fakes import REPO, commit_payload

from app.domain.record_types import RecordType
from app.domain.records import EvidenceRecord

SCHEMA_TABLES = ("evidence_records", "raw_payloads", "ingestion_state")


def make_record(**overrides: object) -> EvidenceRecord:
    defaults: dict = {
        "record_id": "httpx:review_comment:456",
        "record_type": RecordType.REVIEW_COMMENT,
        "repo": REPO,
        "pr_number": 1131,
        "payload": {"body": "retries are opt-in", "line": 243},
        "content_hash": "aabbcc",
        "ingested_at": "2026-10-19T10:00:00Z",
    }
    defaults.update(overrides)
    return EvidenceRecord(**defaults)


@pytest.fixture()
def store(tmp_path):
    from app.ingestion.store import EvidenceStore

    s = EvidenceStore(tmp_path / "evidence.db")
    yield s
    s.close()


class TestSchema:
    def test_required_tables_exist(self, store) -> None:
        rows = store.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {row[0] for row in rows}
        for table in SCHEMA_TABLES:
            assert table in names


class TestUpsertIdempotency:
    def test_same_record_twice_is_one_row(self, store) -> None:
        store.upsert_record(make_record())
        store.upsert_record(make_record())
        assert store.count_records() == 1

    def test_reingest_with_changing_payload_upserts_the_latest_content(self, store) -> None:
        """Full re-ingestion must not leave stale content behind: last
        write wins for the SAME record_id, never duplicated."""
        store.upsert_record(make_record(payload={"body": "v1"}))
        store.upsert_record(make_record(payload={"body": "v1 (edited)"}))
        got = store.get_record("httpx:review_comment:456")
        assert got is not None
        assert got.payload["body"] == "v1 (edited)"
        assert store.count_records() == 1

    def test_distinct_records_coexist(self, store) -> None:
        store.upsert_record(make_record())
        store.upsert_record(make_record(record_id="httpx:review_comment:457"))
        assert store.count_records() == 2


class TestRetrievalShape:
    def test_get_returns_none_for_unknown_id(self, store) -> None:
        assert store.get_record("httpx:pr:99999") is None

    def test_roundtrip_preserves_payload_structure(self, store) -> None:
        record = make_record(
            payload={"path": "httpx/_client.py", "line": 243, "start_line": None, "side": "RIGHT"}
        )
        store.upsert_record(record)
        got = store.get_record(record.record_id)
        assert got is not None
        assert got.payload == record.payload
        assert got.record_type is RecordType.REVIEW_COMMENT

    def test_iter_records_filters_by_type(self, store) -> None:
        store.upsert_record(make_record())
        store.upsert_record(make_record(record_id="httpx:commit:abc",
            record_type=RecordType.COMMIT))
        commits = list(store.iter_records(RecordType.COMMIT))
        assert [r.record_id for r in commits] == ["httpx:commit:abc"]


class TestRawPayloadIsolation:
    def test_raw_and_normalized_do_not_share_space(self, store) -> None:
        """Normalized records are curated; raw payloads are the replay
        archive. Blurring one into the other makes debugging impossible."""
        store.upsert_raw(
            source_id="pulls/1131",
            endpoint="pulls",
            payload=commit_payload("a" * 40, "raw dict preserved as json"),
        )
        assert store.get_raw("pulls/1131") == commit_payload("a" * 40, "raw dict preserved as json")
        assert store.count_records() == 0

    def test_raw_upsert_is_also_idempotent(self, store) -> None:
        store.upsert_raw("pulls/1131", "pulls", {"n": 1})
        store.upsert_raw("pulls/1131", "pulls", {"n": 1})
        store.upsert_raw("pulls/1132", "pulls", {"n": 2})
        assert len(store.raw_ids()) == 2


class TestIngestionState:
    @pytest.mark.parametrize(
        "status", ["pending", "complete", "error"], ids=["pending", "complete", "error"]
    )
    def test_state_roundtrip(self, store, status: str) -> None:
        store.set_pr_state(REPO, 1131, status,
            error=None if status != "error" else "InvalidRecordError: bad payload")
        got = store.get_pr_state(REPO, 1131)
        assert got == status

    def test_unknown_pr_state_is_pending(self, store) -> None:
        assert store.get_pr_state(REPO, 1131) == "pending"

    def test_error_state_carries_the_reason(self, store) -> None:
        store.set_pr_state(REPO, 1132, "error", error="InvalidRecordError: missing id")
        assert store.get_pr_error(REPO, 1132) == "InvalidRecordError: missing id"

    def test_pr_numbers_with_status_filters(self, store) -> None:
        store.set_pr_state(REPO, 1131, "complete")
        store.set_pr_state(REPO, 1132, "pending")
        assert store.pr_numbers_with_status("complete") == [1131]


class TestTransactionalPRWrites:
    def test_buffered_records_are_invisible_before_commit(self, store) -> None:
        store.begin_pr(REPO, 1131)
        store.buffer_record(make_record())
        assert store.get_record("httpx:review_comment:456") is None
        store.commit_pr(REPO, 1131)
        assert store.get_record("httpx:review_comment:456") is not None

    def test_rollback_leaves_zero_trace(self, store) -> None:
        store.begin_pr(REPO, 1132)
        store.buffer_record(make_record(record_id="httpx:review_comment:700"))
        store.rollback_pr(REPO, 1132)
        assert store.get_record("httpx:review_comment:700") is None
        assert store.count_records() == 0
        assert store.get_pr_state(REPO, 1132) == "error"

    def test_set_pr_state_rejects_unknown_status(self, store) -> None:
        from app.core.errors import InvalidRecordError

        with pytest.raises(InvalidRecordError):
            store.set_pr_state(REPO, 1131, "sort-of-done")
