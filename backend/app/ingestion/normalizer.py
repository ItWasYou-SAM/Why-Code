"""Normalization: raw GitHub payloads -> typed EvidenceRecord.

Every artifact is normalized independently and raises ``InvalidRecordError``
on a missing required field. The service calls these inside a per-PR
transaction, so one bad artifact rolls the whole PR back (D-0007).

Payload keys are deliberately kept close to GitHub's vocabulary: decision
extraction later quotes them verbatim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.errors import InvalidRecordError
from app.domain.evidence_ids import (
    commit_id,
    doc_id,
    issue_comment_id,
    issue_id,
    person_id,
    pr_file_id,
    pull_request_id,
    review_comment_id,
    review_id,
)
from app.domain.record_types import RecordType
from app.domain.records import EvidenceRecord, content_hash

REVIEW_STATES = {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED", "PENDING"}


def _fail(record_id: str | None, message: str) -> InvalidRecordError:
    return InvalidRecordError(message, component="ingestion", record_id=record_id)


def _get(blob: dict[str, Any], key: str, *, record_id: str | None = None) -> Any:
    value = blob.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise _fail(record_id, f"missing required field {key!r}")
    return value


def _login(blob: dict[str, Any], *, record_id: str | None = None) -> str:
    user = blob.get("user") or {}
    login = user.get("login") if isinstance(user, dict) else None
    if not login:
        raise _fail(record_id, "missing author login")
    return str(login)


def _make(
    *,
    record_id: str,
    record_type: RecordType,
    repo: str,
    pr_number: int | None,
    payload: dict[str, Any],
) -> EvidenceRecord:
    return EvidenceRecord(
        record_id=record_id,
        record_type=record_type,
        repo=repo,
        pr_number=pr_number,
        payload=payload,
        content_hash=content_hash(payload, record_type),
        ingested_at=datetime.now(UTC),
    )


def normalize_pull_request(repo: str, blob: dict[str, Any]) -> EvidenceRecord:
    number = blob.get("number")
    if not isinstance(number, int):
        raise _fail(None, "pull request is missing a numeric number")
    record_id = pull_request_id(repo=repo, number=number)
    if blob.get("merged") is not True:
        raise _fail(record_id, "pull request is not merged")
    payload = {
        "number": number,
        "title": _get(blob, "title", record_id=record_id),
        "author": _login(blob, record_id=record_id),
        "merged": True,
        "merged_at": _get(blob, "merged_at", record_id=record_id),
        "url": _get(blob, "html_url", record_id=record_id),
        "body": blob.get("body") or "",
        "labels": [
            label.get("name")
            for label in blob.get("labels") or []
            if isinstance(label, dict) and label.get("name")
        ],
    }
    return _make(
        record_id=record_id,
        record_type=RecordType.PULL_REQUEST,
        repo=repo,
        pr_number=number,
        payload=payload,
    )


def normalize_commit(repo: str, pr_number: int, blob: dict[str, Any]) -> EvidenceRecord:
    sha = blob.get("sha")
    if not isinstance(sha, str) or not sha.strip():
        raise _fail(None, "commit is missing a sha")
    record_id = commit_id(repo=repo, sha=sha)
    commit = blob.get("commit") or {}
    author = (commit.get("author") or {}) if isinstance(commit, dict) else {}
    payload = {
        "sha": sha,
        "message": _get(commit, "message", record_id=record_id),
        "author": author.get("name") or _login(blob, record_id=record_id),
        "committed_at": author.get("date") or _get(blob, "committed_at", record_id=record_id),
    }
    return _make(
        record_id=record_id,
        record_type=RecordType.COMMIT,
        repo=repo,
        pr_number=pr_number,
        payload=payload,
    )


def normalize_changed_file(repo: str, pr_number: int, blob: dict[str, Any]) -> EvidenceRecord:
    path = blob.get("filename") or blob.get("path")
    if not isinstance(path, str) or not path.strip():
        raise _fail(None, "changed file is missing a path")
    record_id = pr_file_id(repo=repo, number=pr_number, path=path)
    payload = {
        "path": path,
        "status": blob.get("status") or "modified",
        "additions": int(blob.get("additions") or 0),
        "deletions": int(blob.get("deletions") or 0),
        "patch": blob.get("patch"),
    }
    return _make(
        record_id=record_id,
        record_type=RecordType.CHANGED_FILE,
        repo=repo,
        pr_number=pr_number,
        payload=payload,
    )


def normalize_review(repo: str, pr_number: int, blob: dict[str, Any]) -> EvidenceRecord:
    review_ref = blob.get("id")
    if not isinstance(review_ref, int):
        raise _fail(None, "review is missing a numeric id")
    record_id = review_id(repo=repo, review_id=review_ref)
    state = _get(blob, "state", record_id=record_id)
    if state not in REVIEW_STATES:
        raise _fail(record_id, f"unknown review state {state!r}")
    payload = {
        "state": state,
        "author": _login(blob, record_id=record_id),
        "submitted_at": _get(blob, "submitted_at", record_id=record_id),
        "body": blob.get("body") or "",
    }
    return _make(
        record_id=record_id,
        record_type=RecordType.REVIEW,
        repo=repo,
        pr_number=pr_number,
        payload=payload,
    )


def normalize_review_comment(
    repo: str, pr_number: int, blob: dict[str, Any]
) -> EvidenceRecord:
    comment_ref = blob.get("id")
    if not isinstance(comment_ref, int):
        raise _fail(None, "review comment is missing a numeric id")
    record_id = review_comment_id(repo=repo, comment_id=comment_ref)
    path = _get(blob, "path", record_id=record_id)
    body = _get(blob, "body", record_id=record_id)
    payload = {
        "path": path,
        "line": blob.get("line"),
        "start_line": blob.get("start_line"),
        "side": blob.get("side"),
        "body": body,
        "author": _login(blob, record_id=record_id),
        "created_at": blob.get("created_at"),
        "url": blob.get("html_url"),
        "pull_request_review_id": blob.get("pull_request_review_id"),
    }
    return _make(
        record_id=record_id,
        record_type=RecordType.REVIEW_COMMENT,
        repo=repo,
        pr_number=pr_number,
        payload=payload,
    )


def normalize_issue(
    repo: str, blob: dict[str, Any], *, pr_number: int | None = None
) -> EvidenceRecord:
    number = blob.get("number")
    if not isinstance(number, int):
        raise _fail(None, "issue is missing a numeric number")
    record_id = issue_id(repo=repo, number=number)
    payload = {
        "number": number,
        "title": _get(blob, "title", record_id=record_id),
        "state": _get(blob, "state", record_id=record_id),
        "url": _get(blob, "html_url", record_id=record_id),
        "author": _login(blob, record_id=record_id),
        "body": blob.get("body") or "",
    }
    return _make(
        record_id=record_id,
        record_type=RecordType.ISSUE,
        repo=repo,
        pr_number=pr_number,
        payload=payload,
    )


def normalize_issue_comment(
    repo: str, pr_number: int | None, blob: dict[str, Any]
) -> EvidenceRecord:
    comment_ref = blob.get("id")
    if not isinstance(comment_ref, int):
        raise _fail(None, "issue comment is missing a numeric id")
    record_id = issue_comment_id(repo=repo, comment_id=comment_ref)
    payload = {
        "body": _get(blob, "body", record_id=record_id),
        "author": _login(blob, record_id=record_id),
        "created_at": _get(blob, "created_at", record_id=record_id),
        "url": blob.get("html_url"),
        "issue_number": blob.get("issue_number"),
    }
    return _make(
        record_id=record_id,
        record_type=RecordType.ISSUE_COMMENT,
        repo=repo,
        pr_number=pr_number,
        payload=payload,
    )


def normalize_person(repo: str, login: str) -> EvidenceRecord:
    if not isinstance(login, str) or not login.strip():
        raise _fail(None, "person is missing a login")
    record_id = person_id(repo=repo, login=login)
    return _make(
        record_id=record_id,
        record_type=RecordType.PERSON,
        repo=repo,
        pr_number=None,
        payload={"login": login},
    )


def normalize_doc_file(
    repo: str,
    path: str,
    content: str,
    *,
    pr_number: int | None = None,
    source_sha: str | None = None,
) -> EvidenceRecord:
    if not isinstance(path, str) or not path.strip():
        raise _fail(None, "doc file is missing a path")
    record_id = doc_id(repo=repo, path=path)
    if not isinstance(content, str) or not content.strip():
        raise _fail(record_id, "doc file is empty")
    payload = {
        "path": path,
        "content": content,
        "source_sha": source_sha,
    }
    return _make(
        record_id=record_id,
        record_type=RecordType.DOC_FILE,
        repo=repo,
        pr_number=pr_number,
        payload=payload,
    )
