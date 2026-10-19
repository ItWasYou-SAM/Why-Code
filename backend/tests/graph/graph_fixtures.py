"""Deterministic graph corpus: the exported-JSONL vocabulary the builder
consumes (D-0009: builder's only input shape). Mirrors the payload keys
locked by tests/ingestion/test_normalizer.py — one source of truth for
what a normalized record must contain.

Corpus: PR 1131 (full chain) + PR 1133 (small PR with README doc).
"""

from __future__ import annotations

import json
from typing import Any

REPO = "encode/httpx"
SHORT = "httpx"

DEEP = 1131
SMALL_PR = 1133
DEEP_ISSUE = 108

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
DOC_SHA = "f" * 40
README_SHA = "7" * 40
REGION_ID = f"{SHORT}:region:httpx/_client.py:240:243"

INGESTED_AT = "2026-10-19T10:00:00Z"


def record(
    record_id: str, record_type: str, payload: dict[str, Any], pr_number: int | None
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "record_type": record_type,
        "repo": REPO,
        "pr_number": pr_number,
        "payload": payload,
        "content_hash": "deadbeef",
        "ingested_at": INGESTED_AT,
    }


DEEP_RECORDS: list[dict[str, Any]] = [
    record(
        f"{SHORT}:pr:{DEEP}",
        "pull_request",
        {
            "number": DEEP,
            "title": "Fix retry handling of pool timeouts",
            "author": "tomchristie",
            "merged": True,
            "merged_at": "2024-04-02T06:30:45Z",
            "url": f"https://github.com/{REPO}/pull/{DEEP}",
            "body": "Retries resend POST bodies, tracked in #108",
        },
        DEEP,
    ),
    record(
        f"{SHORT}:commit:{COMMIT_A}",
        "commit",
        {
            "sha": COMMIT_A,
            "message": "Restrict retries to connection errors",
            "author": "tomchristie",
            "committed_at": "2024-03-31T10:00:00Z",
        },
        DEEP,
    ),
    record(
        f"{SHORT}:commit:{COMMIT_B}",
        "commit",
        {
            "sha": COMMIT_B,
            "message": "Plumb timeout config through client",
            "author": "tomchristie",
            "committed_at": "2024-03-31T10:05:00Z",
        },
        DEEP,
    ),
    record(
        f"{SHORT}:pr:{DEEP}:file:httpx/_client.py",
        "changed_file",
        {
            "path": "httpx/_client.py",
            "status": "modified",
            "additions": 12,
            "deletions": 3,
            "patch": "@@ -240,6 +240,9 @@",
        },
        DEEP,
    ),
    record(
        f"{SHORT}:review:101",
        "review",
        {
            "state": "CHANGES_REQUESTED",
            "author": "arbonron",
            "submitted_at": "2024-03-31T11:00:00Z",
        },
        DEEP,
    ),
    record(
        f"{SHORT}:review_comment:456",
        "review_comment",
        {
            "path": "httpx/_client.py",
            "line": 243,
            "start_line": 240,
            "side": "RIGHT",
            "body": "Retries belong on connection errors only, otherwise this resends POST bodies.",
            "author": "arbonron",
        },
        DEEP,
    ),
    record(
        f"{SHORT}:issue:{DEEP_ISSUE}",
        "issue",
        {
            "number": DEEP_ISSUE,
            "title": "Retry policy resends POST bodies",
            "state": "closed",
            "url": f"https://github.com/{REPO}/issues/{DEEP_ISSUE}",
        },
        DEEP,
    ),
    record(
        f"{SHORT}:issue_comment:789",
        "issue_comment",
        {
            "body": "This matches our production outage from March.",
            "author": "jacobk",
            "created_at": "2024-03-30T15:00:00Z",
            "issue_number": DEEP_ISSUE,
        },
        DEEP,
    ),
    record(f"{SHORT}:person:tomchristie", "person", {"login": "tomchristie"}, None),
    record(f"{SHORT}:person:arbonron", "person", {"login": "arbonron"}, None),
    record(f"{SHORT}:person:jacobk", "person", {"login": "jacobk"}, None),
    record(
        f"{SHORT}:doc:docs/api.md",
        "doc_file",
        {
            "path": "docs/api.md",
            "content": "# Retries\nRetries are opt-in.\n",
            "source_sha": DOC_SHA,
        },
        DEEP,
    ),
]

SMALL_PR_RECORDS: list[dict[str, Any]] = [
    record(
        f"{SHORT}:pr:{SMALL_PR}",
        "pull_request",
        {
            "number": SMALL_PR,
            "title": "Fix typo in README",
            "author": "stranger",
            "merged": True,
            "merged_at": "2024-04-04T02:00:00Z",
            "url": f"https://github.com/{REPO}/pull/{SMALL_PR}",
            "body": "",
        },
        SMALL_PR,
    ),
    record(
        f"{SHORT}:commit:{README_SHA[:40]}",
        "commit",
        {
            "sha": README_SHA,
            "message": "Fix typo",
            "author": "stranger",
            "committed_at": "2024-04-03T20:00:00Z",
        },
        SMALL_PR,
    ),
    record(
        f"{SHORT}:pr:{SMALL_PR}:file:README.md",
        "changed_file",
        {
            "path": "README.md",
            "status": "modified",
            "additions": 1,
            "deletions": 1,
            "patch": "@@ -1,1 +1,1 @@",
        },
        SMALL_PR,
    ),
    record(f"{SHORT}:person:stranger", "person", {"login": "stranger"}, None),
    record(
        f"{SHORT}:doc:README.md",
        "doc_file",
        {"path": "README.md", "content": "# Typo fixed\n", "source_sha": README_SHA},
        SMALL_PR,
    ),
]

CORPUS: list[dict[str, Any]] = [*DEEP_RECORDS, *SMALL_PR_RECORDS]


def record_by_id(record_id: str) -> dict[str, Any]:
    for line in CORPUS:
        if line["record_id"] == record_id:
            return line
    raise AssertionError(f"fixture corpus has no record {record_id}")


def jsonl(records: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(r) for r in records)


def dangling_corpus() -> list[dict[str, Any]]:
    """A corpus with a rel whose endpoint is missing (D-0011 test fuel)."""
    broken = record(
        f"{SHORT}:review_comment:999",
        "review_comment",
        {
            "path": "httpx/_client.py",
            "line": 1,
            "start_line": None,
            "side": "RIGHT",
            "body": "points at a review that is not in the corpus",
            "author": "jeez",
            "pull_request_review_id": 999999,
        },
        DEEP,
    )
    return [*DEEP_RECORDS, broken]


EXPECTED_NODE_LABELS = {
    "pull_request": "PullRequest",
    "commit": "Commit",
    "changed_file": "ChangedFile",
    "review": "Review",
    "review_comment": "ReviewComment",
    "issue": "Issue",
    "issue_comment": "IssueComment",
    "person": "Person",
    "doc_file": "Doc",
}


def expected_counts() -> dict[str, dict[str, int]]:
    return {
        "nodes": {
            "Repo": 1,
            "PullRequest": 2,
            "Commit": 3,
            "ChangedFile": 2,
            "Review": 1,
            "ReviewComment": 1,
            "CodeRegion": 1,
            "Issue": 1,
            "IssueComment": 1,
            "Person": 4,
            "Doc": 2,
        },
        "rels": {
            "HAS_PULL_REQUEST": 2,
            "HAS_COMMIT": 3,
            "CHANGED_FILE": 2,
            "HAS_REVIEW": 1,
            "HAS_COMMENT": 2,
            "COMMENTS_ON": 1,
            "HAS_REGION": 1,
            "DISCUSSES": 1,
            "AUTHORED": 5,
            "REVIEWED": 1,
            "COMMENTED": 2,
        },
    }
