"""Evidence validation contracts: existence, membership and graph presence."""

from intent_helpers import REPO

from app.intent.validation.evidence_validator import EvidenceValidator


def record(record_id: str, record_type: str) -> dict:
    return {
        "record_id": record_id,
        "record_type": record_type,
        "repo": REPO,
        "pr_number": 1,
        "payload": {"body": "text"},
        "content_hash": "hash",
        "ingested_at": "2026-10-19T10:00:00+00:00",
    }


class Store:
    def __init__(self, rows):
        from app.domain.records import EvidenceRecord

        self._rows = {
            row["record_id"]: EvidenceRecord.model_validate(row) for row in rows
        }

    def get_record(self, record_id):
        return self._rows.get(record_id)


class TestEvidenceValidator:
    def setup_method(self) -> None:
        self.rows = [
            record("httpx:pr:1", "pull_request"),
            record("httpx:commit:abc", "commit"),
            record("httpx:review_comment:1", "review_comment"),
        ]
        self.store = Store(self.rows)
        self.validator = EvidenceValidator(self.store, require_graph=True)

    def test_valid_citation_passes_all_three_gates(self) -> None:
        result = self.validator.validate(
            ["httpx:review_comment:1", "httpx:commit:abc"],
            allowed_ids={"httpx:review_comment:1", "httpx:commit:abc"},
            graph_evidence_ids={"httpx:review_comment:1", "httpx:commit:abc"},
        )
        assert result.ok
        assert result.accepted_ids == ["httpx:review_comment:1", "httpx:commit:abc"]

    def test_evidence_not_in_store_is_rejected(self) -> None:
        result = self.validator.validate(
            ["httpx:commit:deadbeef"],
            allowed_ids={"httpx:commit:deadbeef"},
            graph_evidence_ids={"httpx:commit:deadbeef"},
        )
        assert not result.ok
        assert result.issues[0].code == "evidence_not_found"

    def test_evidence_outside_the_pack_is_rejected(self) -> None:
        result = self.validator.validate(
            ["httpx:commit:abc"],
            allowed_ids={"httpx:review_comment:1"},
            graph_evidence_ids={"httpx:commit:abc"},
        )
        assert not result.ok
        assert result.issues[0].code == "evidence_outside_pack"

    def test_evidence_missing_from_graph_is_rejected(self) -> None:
        result = self.validator.validate(
            ["httpx:commit:abc"],
            allowed_ids={"httpx:commit:abc"},
            graph_evidence_ids=set(),
        )
        assert not result.ok
        assert result.issues[0].code == "evidence_missing_from_graph"

    def test_empty_evidence_is_rejected(self) -> None:
        result = self.validator.validate(
            [], allowed_ids=set(), graph_evidence_ids=set()
        )
        assert not result.ok
        assert result.issues[0].code == "no_evidence"

    def test_missing_graph_index_blocks_a_write(self) -> None:
        result = self.validator.validate(
            ["httpx:commit:abc"],
            allowed_ids={"httpx:commit:abc"},
            graph_evidence_ids=None,
        )
        assert not result.ok
        assert result.issues[0].code == "graph_index_unavailable"

    def test_one_bad_citation_rejects_the_whole_proposal(self) -> None:
        result = self.validator.validate(
            ["httpx:commit:abc", "httpx:commit:deadbeef"],
            allowed_ids={"httpx:commit:abc", "httpx:commit:deadbeef"},
            graph_evidence_ids={"httpx:commit:abc", "httpx:commit:deadbeef"},
        )
        assert not result.ok
        assert result.accepted_ids == []

    def test_duplicate_citation_is_rejected(self) -> None:
        result = self.validator.validate(
            ["httpx:commit:abc", "httpx:commit:abc"],
            allowed_ids={"httpx:commit:abc"},
            graph_evidence_ids={"httpx:commit:abc"},
        )
        assert not result.ok
        assert result.issues[0].code == "duplicate_evidence"
