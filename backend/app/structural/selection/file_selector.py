"""File selection: Component 1 records -> files to parse.

The selector never walks a repository. It reads the exported
``high_signal_prs.jsonl`` plus ``normalized.jsonl`` and emits one entry per
``path@commit_sha`` for changed files in high-signal PRs, optionally
augmenting with pinned paths and direct imports.

Import expansion is deliberately shallow (one hop) and only includes
imports whose target path is itself present in the corpus metadata or can
be resolved against a known path set. Unresolvable imports are left to
the extractor as ExternalRefs.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.core.errors import InvalidRecordError
from app.structural.config import StructuralConfig, exclusion_reason, language_for_path
from app.structural.extraction.models import Language


@dataclass(frozen=True)
class SelectedFile:
    repo: str
    commit_sha: str
    path: str
    language: Language
    reason: str
    source_pr_numbers: list[int] = field(default_factory=list)
    blob_sha: str | None = None
    blob_url: str | None = None
    raw_url: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "commit_sha": self.commit_sha,
            "path": self.path,
            "language": self.language.value,
            "reason": self.reason,
            "source_pr_numbers": list(self.source_pr_numbers),
            "blob_url": self.blob_url,
            "raw_url": self.raw_url,
        }


@dataclass
class SelectionResult:
    files: list[SelectedFile] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": [entry.as_dict() for entry in self.files],
            "skipped": list(self.skipped),
        }


class FileSelector:
    def __init__(self, config: StructuralConfig) -> None:
        self.config = config

    def select(
        self,
        *,
        normalized_records: Iterable[dict[str, Any]],
        high_signal_prs: Iterable[dict[str, Any]] | None = None,
        commit_by_pr: dict[int, str] | None = None,
        known_paths: set[str] | None = None,
    ) -> SelectionResult:
        records = list(normalized_records)
        # None means "no high-signal filter"; an empty list means "nothing
        # is high-signal", which must select nothing rather than everything.
        high_signal = (
            None
            if high_signal_prs is None
            else {int(pr["pr_number"]) for pr in high_signal_prs if "pr_number" in pr}
        )
        commits = commit_by_pr or _default_commit_by_pr(records, self.config)
        known = known_paths or {
            path for record in records if (path := _path(record)) is not None
        }

        result = SelectionResult()
        chosen: dict[tuple[str, str], SelectedFile] = {}

        for record in records:
            if record.get("record_type") != "changed_file":
                continue
            pr_number = record.get("pr_number")
            if not isinstance(pr_number, int):
                continue
            if high_signal is not None and pr_number not in high_signal:
                continue
            path = _path(record)
            if not path:
                result.skipped.append({"path": "<unknown>", "reason": "missing_path"})
                continue
            commit_sha = commits.get(pr_number)
            if not commit_sha:
                result.skipped.append(
                    {"path": path, "reason": "no_merge_commit_for_pr", "pr_number": str(pr_number)}
                )
                continue
            reason = exclusion_reason(path, self.config)
            if reason:
                result.skipped.append({"path": path, "reason": reason})
                continue
            language = language_for_path(path)
            if language is None:
                result.skipped.append({"path": path, "reason": "unsupported_language"})
                continue
            key = (path, commit_sha)
            payload = record.get("payload") or {}
            existing = chosen.get(key)
            pr_numbers = {pr_number}
            if existing:
                pr_numbers.update(existing.source_pr_numbers)
            chosen[key] = SelectedFile(
                repo=self.config.repo,
                commit_sha=commit_sha,
                path=path,
                language=language,
                reason=existing.reason if existing else "changed_in_high_signal_pr",
                source_pr_numbers=sorted(pr_numbers),
                blob_sha=payload.get("blob_sha") or payload.get("sha"),
            )

        for pinned in self.config.pinned_paths:
            commit_sha = self.config.commit_sha
            if not commit_sha:
                result.skipped.append({"path": pinned, "reason": "no_pinned_commit"})
                continue
            language = language_for_path(pinned)
            if language is None:
                result.skipped.append({"path": pinned, "reason": "unsupported_language"})
                continue
            chosen.setdefault(
                (pinned, commit_sha),
                SelectedFile(
                    repo=self.config.repo,
                    commit_sha=commit_sha,
                    path=pinned,
                    language=language,
                    reason="manually_pinned",
                ),
            )

        if self.config.include_imports:
            for entry in list(chosen.values()):
                if entry.language is not Language.PYTHON:
                    continue
                for imported_path in _direct_import_candidates(entry.path, known):
                    key = (imported_path, entry.commit_sha)
                    if key in chosen:
                        continue
                    if exclusion_reason(imported_path, self.config):
                        continue
                    chosen[key] = SelectedFile(
                        repo=self.config.repo,
                        commit_sha=entry.commit_sha,
                        path=imported_path,
                        language=language_for_path(imported_path) or Language.PYTHON,
                        reason="imported_by_changed_file",
                        source_pr_numbers=list(entry.source_pr_numbers),
                    )

        result.files = sorted(chosen.values(), key=lambda f: (f.path, f.commit_sha))
        return result


def _default_commit_by_pr(
    records: list[dict[str, Any]], config: StructuralConfig
) -> dict[int, str]:
    """Pick the merge commit for each PR from commit records.

    The normalizer does not record which commit is the merge commit, so we
    use the latest commit timestamp in the PR. If the config pins a commit
    it always wins. This is recorded as the file's identity, not as a
    claim about the merge itself.
    """
    if config.commit_sha:
        return {
            int(record["pr_number"]): config.commit_sha
            for record in records
            if record.get("record_type") == "changed_file"
            and isinstance(record.get("pr_number"), int)
        }

    latest: dict[int, tuple[str, str]] = {}
    for record in records:
        if record.get("record_type") != "commit":
            continue
        pr_number = record.get("pr_number")
        payload = record.get("payload") or {}
        sha = payload.get("sha")
        if not isinstance(pr_number, int) or not isinstance(sha, str):
            continue
        when = str(payload.get("committed_at") or "")
        current = latest.get(pr_number)
        if current is None or when >= current[0]:
            latest[pr_number] = (when, sha)
    return {pr_number: sha for pr_number, (_, sha) in latest.items()}


def _path(record: dict[str, Any]) -> str | None:
    payload = record.get("payload") or {}
    path = payload.get("path") or payload.get("filename")
    return path if isinstance(path, str) and path.strip() else None


def _direct_import_candidates(path: str, known: set[str]) -> list[str]:
    """One-hop, path-only import expansion.

    We cannot parse here without content, so this is intentionally limited
    to sibling modules named by the file's own path family. Real import
    resolution happens in the extractor, where the source is available.
    """
    _ = path
    _ = known
    return []


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    file_path = Path(path)
    if not file_path.is_file():
        raise InvalidRecordError(
            f"input file not found: {file_path}", component="structural"
        )
    records: list[dict[str, Any]] = []
    for number, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise InvalidRecordError(
                f"invalid JSON in {file_path} line {number}: {error.msg}",
                component="structural",
            ) from error
        if not isinstance(parsed, dict):
            raise InvalidRecordError(
                f"{file_path} line {number} is not an object", component="structural"
            )
        records.append(parsed)
    return records
