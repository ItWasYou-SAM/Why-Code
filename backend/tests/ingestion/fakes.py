"""Shared fixture vocabulary for Component 1 tests.

Deterministic canned GitHub payloads plus an in-memory GitHubRestClient
double. No network logic here: the real client is exercised separately
with respx. The fake's call log is what lets the suite prove "no
duplicate API calls for already-ingested PRs" and skip-if-complete.

Canonical fixtures:
  PR 1131 "DEEP"    -- decision-dense: core code + config + docs, 2 reviews,
                       3 review comments, 2 issue comments, 3 commits,
                       links issue 108 via "Fixes #108"
  PR 1132 "POISON"  -- one malformed artifact (review_comment without id)
  PR 1133 "TRIVIAL" -- README typo, 1 commit, zero discussion
  Issue 108         -- standalone issue referenced by PR 1131
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from app.core.errors import InvalidRecordError, RateLimitError

REPO = "encode/httpx"

DEEP = 1131
POISON = 1132
TRIVIAL = 1133
DEEP_ISSUE = 108


def pr_payload(
    number: int, merged_at: str, title: str, user: str = "tomchristie"
) -> dict[str, Any]:
    return {
        "id": 1_000_000 + number,
        "number": number,
        "title": title,
        "user": {"login": user},
        "state": "closed",
        "merged": True,
        "merged_at": merged_at,
        "created_at": "2024-03-30T09:00:00Z",
        "updated_at": merged_at,
        "html_url": f"https://github.com/{REPO}/pull/{number}",
        "labels": [{"name": "area:core"}],
        "body": (
            f"Retries resend POST bodies, tracked in #{DEEP_ISSUE}" if number == DEEP else ""
        ),
    }


def file_payload(
    path: str, status: str = "modified", patch: str | None = None, sha: str | None = None
) -> dict[str, Any]:
    return {
        "sha": sha or ("e" * 40 if path.endswith(".py") else "f" * 40),
        "filename": path,
        "status": status,
        "additions": 12 if patch else 1,
        "deletions": 3 if patch else 0,
        "patch": patch,
    }


def commit_payload(sha: str, message: str, author: str = "tomchristie") -> dict[str, Any]:
    return {
        "sha": sha,
        "commit": {
            "message": message,
            "author": {
                "name": author,
                "email": f"{author}@example.com",
                "date": "2024-03-31T10:00:00Z",
            },
        },
        "author": {"login": author},
        "parents": [{"sha": "9" * 40}],
    }


def review_payload(review_id: int, state: str, author: str = "arbonron") -> dict[str, Any]:
    return {
        "id": review_id,
        "user": {"login": author},
        "state": state,
        "submitted_at": "2024-03-31T11:00:00Z",
        "body": "See the redirect handling below." if state == "CHANGES_REQUESTED" else "",
    }


def review_comment_payload(
    comment_id: int,
    path: str,
    line: int | None,
    start_line: int | None,
    side: str,
    body: str,
    author: str = "arbonron",
) -> dict[str, Any]:
    return {
        "id": comment_id,
        "pull_request_review_id": 101,
        "path": path,
        "line": line,
        "start_line": start_line,
        "side": side,
        "user": {"login": author},
        "body": body,
        "created_at": "2024-03-31T11:30:00Z",
        "html_url": f"https://github.com/{REPO}/pull/{DEEP}#discussion_r{comment_id}",
    }


def issue_comment_payload(comment_id: int, body: str, author: str = "jacobk") -> dict[str, Any]:
    return {
        "id": comment_id,
        "user": {"login": author},
        "body": body,
        "created_at": "2024-03-30T15:00:00Z",
        "html_url": f"https://github.com/{REPO}/pull/{DEEP}#issuecomment-{comment_id}",
    }


def issue_payload(number: int, title: str) -> dict[str, Any]:
    return {
        "number": number,
        "title": title,
        "user": {"login": "jacobk"},
        "state": "closed",
        "created_at": "2024-03-01T08:00:00Z",
        "html_url": f"https://github.com/{REPO}/issues/{number}",
    }


DOC_CONTENTS: dict[str, str] = {
    ("f" * 40): "# Retries\nRetries are opt-in; see PR #1131 and the outage thread.\n",
    ("7" * 40): "# Typo fixed\n",
}


def deep_pr() -> dict[str, Any]:
    pr = pr_payload(DEEP, "2024-04-02T06:30:45Z", "Fix retry handling of pool timeouts")
    pr["files"] = [
        file_payload("httpx/_client.py", patch="@@ -240,6 +240,9 @@ def _send_handling_timeouts:"),
        file_payload("httpx/_config.py", patch="@@ -14,3 +14,5 @@ DEFAULT_TIMEOUT_CONFIG = ..."),
        file_payload("docs/api.md", status="modified"),
    ]
    pr["commits"] = [
        commit_payload("a" * 40, "Restrict retries to connection errors"),
        commit_payload("b" * 40, "Plumb timeout config through client"),
        commit_payload("c" * 40, "Update docs for retry semantics"),
    ]
    pr["reviews"] = [
        review_payload(101, "CHANGES_REQUESTED"),
        review_payload(102, "APPROVED"),
    ]
    pr["review_comments"] = [
        review_comment_payload(
            456,
            path="httpx/_client.py",
            line=243,
            start_line=240,
            side="RIGHT",
            body="Retries belong on connection errors only, otherwise this resends POST bodies.",
        ),
        review_comment_payload(
            457,
            path="httpx/_client.py",
            line=250,
            start_line=None,
            side="RIGHT",
            body="Same policy as requests library; document the difference.",
        ),
        review_comment_payload(
            458,
            path="httpx/_config.py",
            line=5,
            start_line=4,
            side="LEFT",
            body="Whitelist the legacy flag here.",
        ),
    ]
    pr["issue_comments"] = [
        issue_comment_payload(789, "This matches our production outage from March."),
        issue_comment_payload(790, "Happy to co-write the changelog entry."),
    ]
    pr["issues"] = [issue_payload(DEEP_ISSUE, "Retry policy resends POST bodies")]
    return pr


def poison_pr() -> dict[str, Any]:
    pr = pr_payload(POISON, "2024-04-03T08:00:00Z", "Bump version policy wording")
    pr["files"] = [file_payload("README.md", patch="@@ -1,1 +1,1 @@")]
    pr["commits"] = [commit_payload("d" * 40, "Fix a typo")]
    pr["reviews"] = []
    pr["review_comments"] = [
        {
            "path": "httpx/_client.py",
            "line": 1,
            "start_line": None,
            "side": "RIGHT",
            "body": "Comment with a *missing id* key — must poison normalization per D-0007.",
        }
    ]
    pr["issue_comments"] = []
    pr["issues"] = []
    return pr


def trivial_pr() -> dict[str, Any]:
    pr = pr_payload(TRIVIAL, "2024-04-04T02:00:00Z", "Fix typo in README", user="stranger")
    pr["files"] = [file_payload("README.md", sha="7" * 40)]
    pr["commits"] = [commit_payload("e2" * 20, "Fix typo")]
    pr["reviews"] = []
    pr["review_comments"] = []
    pr["issue_comments"] = []
    pr["issues"] = []
    return pr


def all_prs() -> list[dict[str, Any]]:
    return [deep_pr(), poison_pr(), trivial_pr()]


class FakeGitHubClient:
    """In-memory double for the real client's protocol.

    Deliberately does NOT simulate server-side filtering: it yields what
    it was given. Window filtering is the service's contract, so the fake
    stays trivial and the real client is tested with respx separately."""

    def __init__(self, prs: list[dict[str, Any]]) -> None:
        self._prs = list(prs)
        self.calls: list[tuple[str, int | None]] = []
        self.blob_failures: set[str] = set()

    def merged_pull_requests(
        self, repo: str, since: str, until: str | None = None
    ) -> Iterator[dict[str, Any]]:
        self.calls.append(("merged_pull_requests", None))
        yield from self._prs

    def pr_detail(self, repo: str, number: int) -> dict[str, Any]:
        self.calls.append(("pr_detail", number))
        return self._pr(number)

    def pr_files(self, repo: str, number: int) -> list[dict[str, Any]]:
        self.calls.append(("pr_files", number))
        return self._pr(number).get("files", [])

    def pr_commits(self, repo: str, number: int) -> list[dict[str, Any]]:
        self.calls.append(("pr_commits", number))
        return self._pr(number).get("commits", [])

    def pr_reviews(self, repo: str, number: int) -> list[dict[str, Any]]:
        self.calls.append(("pr_reviews", number))
        return self._pr(number).get("reviews", [])

    def pr_review_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        self.calls.append(("pr_review_comments", number))
        return self._pr(number).get("review_comments", [])

    def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        self.calls.append(("issue_comments", number))
        return self._pr(number).get("issue_comments", [])

    def linked_issues(self, repo: str, number: int) -> list[dict[str, Any]]:
        self.calls.append(("linked_issues", number))
        return self._pr(number).get("issues", [])

    def blob(self, repo: str, file_sha: str) -> str:
        self.calls.append(("blob", None))
        if file_sha in self.blob_failures:
            raise RateLimitError(
                f"rate limited on blob {file_sha}", component="ingestion", record_id=None
            )
        if file_sha not in DOC_CONTENTS:
            raise InvalidRecordError(
                f"no blob content for sha {file_sha}", component="ingestion", record_id=None
            )
        return DOC_CONTENTS[file_sha]

    def calls_for_pr(self, number: int) -> int:
        return sum(1 for _, pr_number in self.calls if pr_number == number)

    def total_calls(self) -> int:
        return len(self.calls)

    def _pr(self, number: int) -> dict[str, Any]:
        for pr in self._prs:
            if pr["number"] == number:
                return pr
        raise AssertionError(f"fake has no PR {number}")
