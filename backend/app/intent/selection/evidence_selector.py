"""Evidence selection: one compact, replayable bundle per merged PR.

Selection reads only Component 1's exports. It never fetches GitHub and
never invents a record: every item in a bundle carries its normalized
``record_id``, which is the only citation currency the LLM is allowed to
use.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.errors import InvalidRecordError
from app.domain.records import EvidenceRecord
from app.intent.records import (
    author_of,
    content_hash_of,
    load_jsonl,
    payload_of,
    pr_number_of,
    record_id_of,
    record_type_of,
    text_of,
    url_of,
)

RecordLike = EvidenceRecord | dict[str, Any]


@dataclass(frozen=True)
class EvidenceItem:
    record_id: str
    record_type: str
    pr_number: int | None
    payload: dict[str, Any]
    content_hash: str | None = None

    @classmethod
    def from_record(cls, record: RecordLike) -> EvidenceItem:
        return cls(
            record_id=record_id_of(record),
            record_type=record_type_of(record),
            pr_number=pr_number_of(record),
            payload=payload_of(record),
            content_hash=content_hash_of(record),
        )

    @property
    def text(self) -> str:
        return text_of({"payload": self.payload})

    @property
    def author(self) -> str | None:
        return author_of({"payload": self.payload})

    @property
    def url(self) -> str | None:
        return url_of({"payload": self.payload})

    @property
    def occurred_at(self) -> str:
        for key in ("merged_at", "committed_at", "submitted_at", "created_at"):
            value = self.payload.get(key)
            if isinstance(value, str) and value:
                return value
        return ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "record_type": self.record_type,
            "pr_number": self.pr_number,
            "payload": self.payload,
            "content_hash": self.content_hash,
        }


@dataclass
class PrEvidenceBundle:
    repo: str
    pr_number: int
    pr: EvidenceItem
    commits: list[EvidenceItem] = field(default_factory=list)
    files: list[EvidenceItem] = field(default_factory=list)
    reviews: list[EvidenceItem] = field(default_factory=list)
    review_comments: list[EvidenceItem] = field(default_factory=list)
    issues: list[EvidenceItem] = field(default_factory=list)
    issue_comments: list[EvidenceItem] = field(default_factory=list)
    docs: list[EvidenceItem] = field(default_factory=list)
    persons: list[EvidenceItem] = field(default_factory=list)

    def all_items(self) -> list[EvidenceItem]:
        return [
            self.pr,
            *self.commits,
            *self.files,
            *self.reviews,
            *self.review_comments,
            *self.issues,
            *self.issue_comments,
            *self.docs,
        ]

    def items_by_id(self) -> dict[str, EvidenceItem]:
        return {item.record_id: item for item in self.all_items()}

    def evidence_ids(self) -> list[str]:
        return sorted(self.items_by_id())

    def changed_paths(self) -> set[str]:
        return {
            str(item.payload.get("path"))
            for item in self.files
            if item.payload.get("path")
        }

    def latest_commit(self) -> EvidenceItem | None:
        if not self.commits:
            return None
        return max(
            self.commits,
            key=lambda item: (
                str(item.payload.get("committed_at") or ""),
                item.record_id,
            ),
        )

    def latest_commit_sha(self) -> str | None:
        commit = self.latest_commit()
        if commit is None:
            return None
        sha = commit.payload.get("sha")
        return str(sha) if isinstance(sha, str) and sha else None

    def review_participants(self) -> set[str]:
        author = self.pr.author
        logins = {
            item.author
            for item in [*self.reviews, *self.review_comments, *self.issue_comments]
            if item.author
        }
        logins.discard(author)
        return logins

    def issue_discussion_authors(self) -> set[str]:
        return {
            item.author
            for item in self.issue_comments
            if item.author and item.payload.get("issue_number") is not None
        }

    def linked_issue_ids(self) -> set[str]:
        return {item.record_id for item in self.issues}

    def summary(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "pr_number": self.pr_number,
            "title": self.pr.payload.get("title", ""),
            "merged_at": self.pr.payload.get("merged_at"),
            "evidence_ids": self.evidence_ids(),
            "counts": {
                "commits": len(self.commits),
                "files": len(self.files),
                "reviews": len(self.reviews),
                "review_comments": len(self.review_comments),
                "issues": len(self.issues),
                "issue_comments": len(self.issue_comments),
                "docs": len(self.docs),
            },
        }


class EvidenceSelector:
    """Group normalized records into deterministically ordered PR bundles."""

    def __init__(
        self,
        repo: str,
        *,
        high_signal_pr_numbers: set[int] | None = None,
        max_prs: int | None = None,
    ) -> None:
        self.repo = repo
        # ``None`` means "no filter"; an empty set means "nothing selected".
        self.high_signal_pr_numbers = high_signal_pr_numbers
        self.max_prs = max_prs

    @classmethod
    def from_jsonl(
        cls,
        *,
        repo: str,
        normalized_path: str | Path,
        high_signal_path: str | Path | None = None,
        max_prs: int | None = None,
    ) -> EvidenceSelector:
        high_signal: set[int] | None = None
        if high_signal_path is not None:
            high_signal = high_signal_numbers(load_jsonl(high_signal_path))
        return cls(
            repo,
            high_signal_pr_numbers=high_signal,
            max_prs=max_prs,
        )

    def select(
        self,
        records: Sequence[RecordLike],
        *,
        high_signal_records: list[dict[str, Any]] | None = None,
    ) -> list[PrEvidenceBundle]:
        if high_signal_records is not None:
            self.high_signal_pr_numbers = high_signal_numbers(high_signal_records)

        materialized = [EvidenceItem.from_record(record) for record in records]
        _assert_repo(materialized, self.repo)
        prs = {
            int(item.payload["number"]): item
            for item in materialized
            if item.record_type == "pull_request"
            and isinstance(item.payload.get("number"), int)
            and item.payload.get("merged") is True
        }
        if self.high_signal_pr_numbers is not None:
            prs = {
                number: item
                for number, item in prs.items()
                if number in self.high_signal_pr_numbers
            }
        numbers = sorted(prs)
        if self.max_prs is not None:
            numbers = numbers[: self.max_prs]

        by_pr: dict[int, list[EvidenceItem]] = {number: [] for number in numbers}
        persons_by_login: dict[str, EvidenceItem] = {}
        for item in materialized:
            if item.record_type == "person":
                login = item.payload.get("login")
                if isinstance(login, str):
                    persons_by_login[login] = item
                continue
            number = item.pr_number
            if number in by_pr:
                by_pr[number].append(item)

        bundles: list[PrEvidenceBundle] = []
        for number in numbers:
            items = by_pr[number]
            pr = prs[number]
            issues = [item for item in items if item.record_type == "issue"]
            issue_numbers = {
                int(item.payload["number"])
                for item in issues
                if isinstance(item.payload.get("number"), int)
            }
            issue_comments = [
                item
                for item in items
                if item.record_type == "issue_comment"
                and (
                    item.pr_number == number
                    or (
                        isinstance(item.payload.get("issue_number"), int)
                        and int(item.payload["issue_number"]) in issue_numbers
                    )
                )
            ]
            bundle = PrEvidenceBundle(
                repo=self.repo,
                pr_number=number,
                pr=pr,
                commits=sorted(
                    (i for i in items if i.record_type == "commit"),
                    key=lambda i: i.record_id,
                ),
                files=sorted(
                    (i for i in items if i.record_type == "changed_file"),
                    key=lambda i: i.record_id,
                ),
                reviews=sorted(
                    (i for i in items if i.record_type == "review"),
                    key=lambda i: i.record_id,
                ),
                review_comments=sorted(
                    (i for i in items if i.record_type == "review_comment"),
                    key=lambda i: i.record_id,
                ),
                issues=sorted(issues, key=lambda i: i.record_id),
                issue_comments=sorted(issue_comments, key=lambda i: i.record_id),
                docs=sorted(
                    (i for i in items if i.record_type == "doc_file"),
                    key=lambda i: i.record_id,
                ),
            )
            logins = {
                item.author
                for item in bundle.all_items()
                if item.author is not None
            }
            bundle.persons = sorted(
                (
                    persons_by_login[login]
                    for login in logins
                    if login in persons_by_login
                ),
                key=lambda i: i.record_id,
            )
            bundles.append(bundle)
        return bundles


def high_signal_numbers(rows: list[dict[str, Any]]) -> set[int]:
    numbers: set[int] = set()
    for row in rows:
        number = row.get("pr_number")
        if isinstance(number, int):
            numbers.add(number)
    return numbers


def _assert_repo(items: list[EvidenceItem], repo: str) -> None:
    del items  # EvidenceItem intentionally does not duplicate repo scope.
    if not repo or not repo.strip():
        raise InvalidRecordError("repo must be non-empty", component="intent")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


__all__ = [
    "EvidenceItem",
    "EvidenceSelector",
    "PrEvidenceBundle",
    "high_signal_numbers",
    "utc_now",
]
