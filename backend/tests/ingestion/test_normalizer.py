"""Normalizer contracts: every GitHub artifact becomes a typed
EvidenceRecord with the right ID, type, and required payload fields.

Per D-0007/D-0008: repo token in IDs is the short name; missing required
payload keys raise InvalidRecordError rather than being silently dropped.
"""

import pytest
from fakes import (
    REPO,
    commit_payload,
    file_payload,
    issue_comment_payload,
    issue_payload,
    pr_payload,
    review_comment_payload,
    review_payload,
)

from app.core.errors import InvalidRecordError


def _require_keys(record, keys):
    missing = [k for k in keys if k not in record.payload]
    assert not missing, f"payload missing {missing}"


class TestPullRequestNormalization:
    def test_pr_record_has_canonical_id_and_type(self) -> None:
        from app.domain.record_types import RecordType
        from app.ingestion.normalizer import normalize_pull_request

        record = normalize_pull_request(REPO, pr_payload(1131, "2024-04-02T06:30:45Z",
            "Fix retries"))
        assert record.record_id == "httpx:pr:1131"
        assert record.record_type is RecordType.PULL_REQUEST
        assert record.repo == REPO

    def test_pr_payload_keeps_the_fields_decisions_cite(self) -> None:
        from app.ingestion.normalizer import normalize_pull_request

        payload = pr_payload(1131, "2024-04-02T06:30:45Z", "Fix retry handling of pool timeouts")
        payload["body"] = "Fixes #108"
        record = normalize_pull_request(REPO, payload)
        _require_keys(record, ["number", "title", "author", "merged", "merged_at", "url", "body"])
        assert record.payload["merged_at"] == "2024-04-02T06:30:45Z"

    def test_pr_without_number_is_rejected(self) -> None:
        from app.ingestion.normalizer import normalize_pull_request

        payload = pr_payload(1131, "2024-04-02T06:30:45Z", "x")
        del payload["number"]
        with pytest.raises(InvalidRecordError):
            normalize_pull_request(REPO, payload)

    def test_unmerged_pr_is_rejected(self) -> None:
        from app.ingestion.normalizer import normalize_pull_request

        payload = pr_payload(1131, "2024-04-02T06:30:45Z", "closed but unmerged")
        payload["merged"] = False
        with pytest.raises(InvalidRecordError):
            normalize_pull_request(REPO, payload)


class TestCommitNormalization:
    def test_commit_record_keeps_sha_id(self) -> None:
        from app.domain.record_types import RecordType
        from app.ingestion.normalizer import normalize_commit

        record = normalize_commit(REPO, 1131, commit_payload("a" * 40,
            "Restrict retries to connection errors"))
        assert record.record_id == f"httpx:commit:{'a' * 40}"
        assert record.record_type is RecordType.COMMIT
        assert record.pr_number == 1131

    def test_commit_message_and_author_survive(self) -> None:
        from app.ingestion.normalizer import normalize_commit

        record = normalize_commit(REPO, 1131, commit_payload("a" * 40, "Restrict retries",
            author="tomx"))
        _require_keys(record, ["sha", "message", "author", "committed_at"])
        assert record.payload["message"] == "Restrict retries"

    def test_commit_without_sha_is_rejected(self) -> None:
        from app.ingestion.normalizer import normalize_commit

        with pytest.raises(InvalidRecordError):
            normalize_commit(REPO, 1131, {"commit": {"message": "no sha here"}})


class TestChangedFileNormalization:
    def test_changed_file_id_pairs_pr_and_path(self) -> None:
        from app.domain.record_types import RecordType
        from app.ingestion.normalizer import normalize_changed_file

        record = normalize_changed_file(REPO, 1131, file_payload("httpx/_client.py",
            patch="@@ -1 +1 @@"))
        assert record.record_id == "httpx:pr:1131:file:httpx/_client.py"
        assert record.record_type is RecordType.CHANGED_FILE

    def test_changed_file_keeps_patch_when_present(self) -> None:
        from app.ingestion.normalizer import normalize_changed_file

        record = normalize_changed_file(REPO, 1131, file_payload("httpx/_client.py",
            patch="@@ -240 +240 @@"))
        _require_keys(record, ["path", "status", "additions", "deletions", "patch"])

    def test_changed_file_without_path_is_rejected(self) -> None:
        from app.ingestion.normalizer import normalize_changed_file

        blob = file_payload("httpx/_client.py")
        blob["filename"] = None
        with pytest.raises(InvalidRecordError):
            normalize_changed_file(REPO, 1131, blob)


class TestReviewNormalization:
    def test_review_record(self) -> None:
        from app.domain.record_types import RecordType
        from app.ingestion.normalizer import normalize_review

        record = normalize_review(REPO, 1131, review_payload(101, "CHANGES_REQUESTED"))
        assert record.record_id == "httpx:review:101"
        assert record.record_type is RecordType.REVIEW
        _require_keys(record, ["state", "author", "submitted_at"])

    def test_review_scratch_state_is_rejected(self) -> None:
        from app.ingestion.normalizer import normalize_review

        with pytest.raises(InvalidRecordError):
            normalize_review(REPO, 1131, review_payload(101, "SCRATCH"))


class TestReviewCommentNormalization:
    def test_review_comment_retains_position_fields_no_matter_what(self) -> None:
        """Plan-level hard requirement: path, line, start_line, side survive
        normalization — even when null — or line-anchored CODE_REGION links
        become impossible downstream"""
        from app.domain.record_types import RecordType
        from app.ingestion.normalizer import normalize_review_comment

        for line, start_line in ((243, 240), (250, None), (None, None)):
            record = normalize_review_comment(
                REPO, 1131, review_comment_payload(456, "httpx/_client.py", line, start_line,
                    "RIGHT", "why?")
            )
            assert record.record_id == "httpx:review_comment:456"
            assert record.record_type is RecordType.REVIEW_COMMENT
            for key in ("path", "line", "start_line", "side"):
                assert key in record.payload
            assert record.payload["line"] == line
            assert record.payload["start_line"] == start_line
            assert record.payload["side"] == "RIGHT"

    def test_review_comment_with_empty_body_is_rejected(self) -> None:
        from app.ingestion.normalizer import normalize_review_comment

        blob = review_comment_payload(456, "httpx/_client.py", 1, None, "RIGHT", "")
        blob["body"] = "   "
        with pytest.raises(InvalidRecordError):
            normalize_review_comment(REPO, 1131, blob)

    def test_review_comment_attachment_requested(self) -> None:
        """A comment cannot anchor a decision to code without a path."""
        from app.ingestion.normalizer import normalize_review_comment

        blob = review_comment_payload(456, "httpx/_client.py", 1, None, "RIGHT", "why?")
        del blob["path"]
        with pytest.raises(InvalidRecordError):
            normalize_review_comment(REPO, 1131, blob)


class TestIssueAndCommentNormalization:
    def test_issue_record(self) -> None:
        from app.domain.record_types import RecordType
        from app.ingestion.normalizer import normalize_issue

        record = normalize_issue(REPO, issue_payload(108, "Retry policy resends POST bodies"))
        assert record.record_id == "httpx:issue:108"
        assert record.record_type is RecordType.ISSUE
        _require_keys(record, ["number", "title", "state", "url"])

    def test_issue_comment_record(self) -> None:
        from app.domain.record_types import RecordType
        from app.ingestion.normalizer import normalize_issue_comment

        record = normalize_issue_comment(REPO, 1131, issue_comment_payload(789,
            "Matches our outage"))
        assert record.record_id == "httpx:issue_comment:789"
        assert record.record_type is RecordType.ISSUE_COMMENT
        assert record.pr_number == 1131
        _require_keys(record, ["body", "author", "created_at"])


class TestPersonAndDoc:
    def test_person_record(self) -> None:
        from app.domain.record_types import RecordType
        from app.ingestion.normalizer import normalize_person

        record = normalize_person(REPO, "tomchristie")
        assert record.record_id == "httpx:person:tomchristie"
        assert record.record_type is RecordType.PERSON
        assert record.payload["login"] == "tomchristie"

    def test_doc_file_record(self) -> None:
        from app.domain.record_types import RecordType
        from app.ingestion.normalizer import normalize_doc_file

        record = normalize_doc_file(REPO, "docs/api.md", content="# Retries\nSee PR #1131.\n")
        assert record.record_id == "httpx:doc:docs/api.md"
        assert record.record_type is RecordType.DOC_FILE
        assert "Retries" in record.payload["content"]

    def test_empty_doc_is_rejected(self) -> None:
        from app.ingestion.normalizer import normalize_doc_file

        with pytest.raises(InvalidRecordError):
            normalize_doc_file(REPO, "docs/api.md", "  \n")
