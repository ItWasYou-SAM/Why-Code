"""Contract for EvidenceRecord: the single normalized unit every
component speaks. Ingestion writes them, the graph builder consumes them,
retrieval cites them. Content-hash stability is critical for idempotency
and for the regrounding check (D-0004)."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.record_types import RecordType
from app.domain.records import EvidenceRecord, content_hash


def build_record(**overrides: object) -> EvidenceRecord:
    defaults: dict = {
        "record_id": "httpx:review_comment:456",
        "record_type": RecordType.REVIEW_COMMENT,
        "repo": "encode/httpx",
        "pr_number": 1131,
        "payload": {"body": "retries are opt-in", "author": "tomchristie"},
        "content_hash": "b0766a1e60fa254b",
        "ingested_at": datetime(2026, 10, 19, tzinfo=UTC),
    }
    defaults.update(overrides)
    return EvidenceRecord(**defaults)


class TestRecordType:
    def test_all_nine_record_types_exist_with_exact_values(self) -> None:
        values = {member.value for member in RecordType}
        assert values == {
            "pull_request",
            "commit",
            "changed_file",
            "review",
            "review_comment",
            "issue",
            "issue_comment",
            "person",
            "doc_file",
        }

    def test_values_are_serializable_strings(self) -> None:
        assert RecordType.REVIEW_COMMENT.value == "review_comment"


class TestEvidenceRecordShape:
    def test_minimum_valid_record_constructs(self) -> None:
        record = build_record()
        assert record.record_id == "httpx:review_comment:456"
        assert record.repo == "encode/httpx"
        assert record.pr_number == 1131

    def test_pr_number_allowed_to_be_none_for_repo_level_records(self) -> None:
        record = build_record(record_id="httpx:commit:abc123", pr_number=None)
        assert record.pr_number is None

    def test_negative_pr_number_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            build_record(pr_number=-1)

    def test_unknown_record_type_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            build_record(record_type="tweet")

    def test_empty_repo_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            build_record(repo="")

    def test_payload_must_be_present_even_if_empty_object(self) -> None:
        assert build_record(payload={}).payload == {}
        with pytest.raises(ValidationError):
            build_record(payload=None)

    def test_content_hash_is_required_never_inferred(self) -> None:
        """Ingestion computes the hash explicitly. Auto-inference hides a
        defective hashing pipeline; the field stays mandatory."""
        with pytest.raises(ValidationError):
            build_record(content_hash=None)


class TestContentHashFunction:
    def test_hash_is_stable_across_payload_key_ordering(self) -> None:
        """Idempotent re-ingestion must not mutate the hash, or reruns
        upsert new rows and the dedupe guarantee dies."""
        left = content_hash({"body": "x", "author": "y"})
        right = content_hash({"author": "y", "body": "x"})
        assert left == right

    def test_hash_changes_when_payload_content_changes(self) -> None:
        left = content_hash({"body": "v1"})
        right = content_hash({"body": "v2"})
        assert left != right

    def test_hash_is_scoped_to_record_type(self) -> None:
        """Same payload under two record types must hash differently:
        a comment promoted to another record kind is not the same record."""
        left = content_hash({"body": "x"}, record_type="review_comment")
        right = content_hash({"body": "x"}, record_type="issue_comment")
        assert left != right
