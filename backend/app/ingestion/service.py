"""Ingestion orchestration.

One PR is one atomic unit (D-0007): normalize every artifact into memory,
fetch doc blobs, then write the whole buffer in a single transaction. Any
failure rolls the PR back and marks it errored; the run continues. The
service never stops because one PR is poisoned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.errors import AppError, InvalidRecordError
from app.domain.records import EvidenceRecord
from app.ingestion.normalizer import (
    normalize_changed_file,
    normalize_commit,
    normalize_doc_file,
    normalize_issue,
    normalize_issue_comment,
    normalize_person,
    normalize_pull_request,
    normalize_review,
    normalize_review_comment,
)
from app.ingestion.scorer import RelevanceScorer, ScoreResult
from app.ingestion.store import EvidenceStore

DOC_SUFFIXES = (".md", ".markdown", ".rst")


@dataclass
class RunResult:
    repo: str
    processed: list[int] = field(default_factory=list)
    completed: list[int] = field(default_factory=list)
    errored: list[int] = field(default_factory=list)
    scored: dict[int, ScoreResult] = field(default_factory=dict)
    api_calls: int = 0
    started_at: str = ""
    finished_at: str = ""

    def scored_payload(self) -> dict[int, dict[str, Any]]:
        return {
            number: {"score": result.score, "band": result.band, "reasons": result.reasons}
            for number, result in self.scored.items()
        }


class IngestionService:
    def __init__(
        self,
        *,
        github: Any,
        store: EvidenceStore,
        repo: str,
        since: str,
        limit: int = 100,
        scorer: RelevanceScorer | None = None,
    ) -> None:
        self.github = github
        self.store = store
        self.repo = repo
        self.since = since
        self.limit = limit
        self.scorer = scorer or RelevanceScorer()

    def run(self, *, force: bool = False) -> RunResult:
        started = datetime.now(UTC).isoformat()
        result = RunResult(repo=self.repo, started_at=started)
        since = _parse_timestamp(self.since)

        ingested = 0
        for pr in self.github.merged_pull_requests(self.repo, since=self.since):
            if ingested >= self.limit:
                break
            number = pr.get("number")
            if not isinstance(number, int):
                continue
            if not _is_merged(pr):
                continue
            merged_at = _parse_timestamp(str(pr.get("merged_at") or ""))
            if merged_at is None:
                continue
            if since is not None and merged_at < since:
                continue
            if not force and self.store.get_pr_state(self.repo, number) == "complete":
                continue

            result.processed.append(number)
            ingested += 1
            try:
                bundle = self._fetch_bundle(number)
                records = self._normalize_bundle(number, bundle)
                self.store.begin_pr(self.repo, number)
                try:
                    for record in records:
                        self.store.buffer_record(record)
                    self.store.commit_pr(self.repo, number)
                except Exception:
                    self.store.rollback_pr(self.repo, number, error="write failed")
                    raise
                result.completed.append(number)
                result.scored[number] = self.scorer.score(bundle)
            except Exception as error:  # noqa: BLE001 - one bad PR never stops a run
                if isinstance(error, AppError):
                    reason = f"{type(error).__name__}: {error.message}"
                else:
                    reason = f"{type(error).__name__}: {error}"
                if self.store.get_pr_state(self.repo, number) != "error":
                    self.store.set_pr_state(self.repo, number, "error", error=reason)
                result.errored.append(number)

        result.api_calls = _call_count(self.github)
        result.finished_at = datetime.now(UTC).isoformat()
        return result

    # -- fetching -------------------------------------------------------
    def _fetch_bundle(self, number: int) -> dict[str, Any]:
        pr = self.github.pr_detail(self.repo, number)
        files = self.github.pr_files(self.repo, number)
        commits = self.github.pr_commits(self.repo, number)
        reviews = self.github.pr_reviews(self.repo, number)
        review_comments = self.github.pr_review_comments(self.repo, number)
        issue_comments = self.github.issue_comments(self.repo, number)
        issues = self.github.linked_issues(self.repo, number)
        return {
            "pr": pr,
            "files": files,
            "commits": commits,
            "reviews": reviews,
            "review_comments": review_comments,
            "issue_comments": issue_comments,
            "issues": issues,
        }

    # -- normalization --------------------------------------------------
    def _normalize_bundle(self, number: int, bundle: dict[str, Any]) -> list[EvidenceRecord]:
        pr = bundle["pr"]
        records: list[EvidenceRecord] = [normalize_pull_request(self.repo, pr)]
        authors: set[str] = set()

        for blob in bundle["commits"]:
            record = normalize_commit(self.repo, number, blob)
            records.append(record)
            authors.add(str(record.payload["author"]))
        for blob in bundle["files"]:
            records.append(normalize_changed_file(self.repo, number, blob))
        for blob in bundle["reviews"]:
            record = normalize_review(self.repo, number, blob)
            records.append(record)
            authors.add(str(record.payload["author"]))
        for blob in bundle["review_comments"]:
            record = normalize_review_comment(self.repo, number, blob)
            records.append(record)
            authors.add(str(record.payload["author"]))
        for blob in bundle["issue_comments"]:
            record = normalize_issue_comment(self.repo, number, blob)
            records.append(record)
            authors.add(str(record.payload["author"]))

        issues = bundle["issues"]
        single_issue_number = (
            issues[0].get("number") if len(issues) == 1 and isinstance(issues[0], dict) else None
        )
        if single_issue_number is not None:
            for record in records:
                if record.record_type.value == "issue_comment" and not record.payload.get(
                    "issue_number"
                ):
                    record.payload["issue_number"] = single_issue_number

        for blob in issues:
            records.append(normalize_issue(self.repo, blob, pr_number=number))
            author = (blob.get("user") or {}).get("login") if isinstance(blob, dict) else None
            if author:
                authors.add(str(author))

        pr_author = (pr.get("user") or {}).get("login") if isinstance(pr, dict) else None
        if pr_author:
            authors.add(str(pr_author))

        # Documents are fetched only after every artifact normalized: a
        # poisoned PR must not reach the blob API (see docs tests).
        for blob in bundle["files"]:
            path = str(blob.get("filename") or blob.get("path") or "")
            if not _is_doc(path):
                continue
            sha = blob.get("sha")
            if not isinstance(sha, str) or not sha:
                raise InvalidRecordError(
                    f"doc file {path!r} has no blob sha",
                    component="ingestion",
                    record_id=f"{self.repo}:pr:{number}:file:{path}",
                )
            content = self.github.blob(self.repo, sha)
            records.append(
                normalize_doc_file(
                    self.repo, path, content, pr_number=number, source_sha=sha
                )
            )

        for login in sorted(authors):
            records.append(normalize_person(self.repo, login))

        _assert_unique(records)
        return records


def _is_doc(path: str) -> bool:
    lowered = path.lower()
    if not lowered.endswith(DOC_SUFFIXES):
        return False
    return lowered.startswith("docs/") or lowered.rsplit("/", 1)[-1].startswith("readme")


def _assert_unique(records: list[EvidenceRecord]) -> None:
    seen: set[str] = set()
    for record in records:
        if record.record_id in seen:
            raise InvalidRecordError(
                f"duplicate record_id in one PR: {record.record_id}",
                component="ingestion",
                record_id=record.record_id,
            )
        seen.add(record.record_id)


def _is_merged(pr: dict[str, Any]) -> bool:
    return pr.get("merged") is True and bool(pr.get("merged_at"))


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _call_count(github: Any) -> int:
    if hasattr(github, "total_calls"):
        return int(github.total_calls())
    calls = getattr(github, "calls", None)
    return len(calls) if isinstance(calls, list) else 0
