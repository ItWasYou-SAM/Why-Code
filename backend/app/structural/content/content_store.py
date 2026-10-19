"""Content at the merge commit, stored outside the graph.

Phase 2 of the structural pass: fetch a selected file's bytes *at its
commit*, never at a branch tip, and persist them in local artifact storage:

* the full text goes to a content-addressed blob
  ``<root>/<repo>/<sha256>.txt`` (identical content is written once), and
* a small :class:`ContentRecord` (identity + hash + size + line count +
  ``blob_path``) goes into a JSONL manifest.

The content itself is never a node property. Neo4j holds graph structure and
the small retrieval-facing metadata; the codebase lives on disk.

An existing manifest is also the replay cache: if a record for the same
``path@commit_sha`` is present and its blob still exists, the bytes are read
from disk instead of fetched again. Replay is therefore deterministic and
does not depend on network availability.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.core.errors import InvalidRecordError
from app.structural.content.file_content_fetcher import ContentFetcher
from app.structural.content.metrics import content_sha256, count_lines, utf8_size
from app.structural.extraction.ids import file_id
from app.structural.extraction.models import Language


class ContentRecord(BaseModel):
    """Metadata for one file-at-a-commit. Never carries the content itself."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    file_id: str
    repo: str
    path: str
    commit_sha: str
    language: Language
    content_hash: str
    byte_size: int
    line_count: int
    blob_path: str


@dataclass(frozen=True)
class StoredContent:
    """A content record plus the bytes it describes (for the extractor)."""

    record: ContentRecord
    content: str


class ContentStore:
    def __init__(
        self,
        fetcher: ContentFetcher,
        root: str | Path,
        *,
        manifest_path: str | Path | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.root = Path(root)
        self.records: list[ContentRecord] = []
        self._known: dict[tuple[str, str, str], ContentRecord] = {}
        if manifest_path is not None:
            self._load_manifest(Path(manifest_path))

    def store(
        self,
        *,
        repo: str,
        path: str,
        commit_sha: str,
        language: Language,
        max_bytes: int | None = None,
    ) -> StoredContent:
        key = (repo, path, commit_sha)
        cached = self._known.get(key)
        if cached is not None:
            blob = self.root / cached.blob_path
            if blob.is_file():
                return StoredContent(
                    record=cached, content=blob.read_text(encoding="utf-8")
                )

        content = self.fetcher.fetch(repo=repo, path=path, commit_sha=commit_sha)
        size = utf8_size(content)
        if max_bytes is not None and size > max_bytes:
            raise InvalidRecordError(
                "file_too_large",
                component="structural",
                record_id=file_id(repo=repo, path=path, commit_sha=commit_sha),
            )
        digest = content_sha256(content)
        blob = self._blob_path(repo, digest)
        if not blob.is_file():
            blob.parent.mkdir(parents=True, exist_ok=True)
            blob.write_text(content, encoding="utf-8")
        record = ContentRecord(
            file_id=file_id(repo=repo, path=path, commit_sha=commit_sha),
            repo=repo,
            path=path,
            commit_sha=commit_sha,
            language=language,
            content_hash=digest,
            byte_size=size,
            line_count=count_lines(content),
            blob_path=str(blob.relative_to(self.root)),
        )
        self.records.append(record)
        self._known[key] = record
        return StoredContent(record=record, content=content)

    def write_manifest(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        ordered = sorted(self.records, key=lambda record: (record.path, record.commit_sha))
        out.write_text(
            "".join(record.model_dump_json() + "\n" for record in ordered),
            encoding="utf-8",
        )
        return out

    def _load_manifest(self, path: Path) -> None:
        if not path.is_file():
            return
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                record = ContentRecord.model_validate(json.loads(line))
            except ValueError as error:
                raise InvalidRecordError(
                    f"invalid content manifest line {number}: {error}",
                    component="structural",
                ) from error
            self.records.append(record)
            self._known[(record.repo, record.path, record.commit_sha)] = record

    def _blob_path(self, repo: str, digest: str) -> Path:
        safe_repo = repo.replace("/", "__").strip("_") or "repo"
        return self.root / safe_repo / f"{digest}.txt"


__all__ = ["ContentRecord", "ContentStore", "StoredContent"]
