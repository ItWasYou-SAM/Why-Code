"""Structural pipeline orchestration.

    selected files -> content at commit -> per-file extraction
                   -> graph records -> validation -> (optional) Neo4j write

A file that cannot be fetched or parsed is logged as skipped, never
partially represented. This is the Component 3 acceptance rule "no node
for an unparseable file; it is logged instead".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import InvalidRecordError
from app.structural.config import StructuralConfig
from app.structural.content.content_store import ContentRecord, ContentStore
from app.structural.content.file_content_fetcher import ContentFetcher
from app.structural.content.metrics import utf8_size
from app.structural.extraction.file_extractor import FileExtractor, doc_mentions
from app.structural.extraction.models import (
    ConfigKey,
    DocMention,
    Language,
    StructuralBatch,
    StructuralFile,
)
from app.structural.graph.graph_records import StructuralGraph, build_structural_graph
from app.structural.graph.neo4j_writer import StructuralGraphWriter, WriteReport
from app.structural.selection.file_selector import SelectionResult
from app.structural.validation.graph_validator import (
    ValidationReport,
    validate_batch,
    validate_graph,
)


@dataclass
class StructuralResult:
    batch: StructuralBatch
    graph: StructuralGraph
    validation: ValidationReport
    write_report: WriteReport | None = None
    selection: SelectionResult | None = None
    skipped: list[dict[str, str]] = field(default_factory=list)
    content: list[ContentRecord] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.batch.repo,
            "files": len(self.batch.files),
            "content_records": len(self.content),
            "validation": self.validation.as_dict(),
            "graph": self.graph.counts(),
            "write": self.write_report.as_dict() if self.write_report else None,
            "skipped": self.skipped,
        }


class StructuralPipeline:
    def __init__(
        self,
        *,
        config: StructuralConfig,
        fetcher: ContentFetcher,
        extractor: FileExtractor | None = None,
        writer: StructuralGraphWriter | None = None,
        content_store: ContentStore | None = None,
    ) -> None:
        self.config = config
        self.fetcher = fetcher
        self.extractor = extractor or FileExtractor()
        self.writer = writer
        self.content_store = content_store

    def _read_content(
        self, *, repo: str, path: str, commit_sha: str, language: Language
    ) -> str:
        """Fetch a file at its commit. Over the byte cap, refuse — never truncate."""
        if self.content_store is not None:
            return self.content_store.store(
                repo=repo,
                path=path,
                commit_sha=commit_sha,
                language=language,
                max_bytes=self.config.max_file_bytes,
            ).content
        source = self.fetcher.fetch(repo=repo, path=path, commit_sha=commit_sha)
        if utf8_size(source) > self.config.max_file_bytes:
            raise InvalidRecordError("file_too_large", component="structural")
        return source

    def run(self, selection: SelectionResult) -> StructuralResult:
        known_paths = {entry.path for entry in selection.files}
        batch = StructuralBatch(repo=self.config.repo)
        skipped: list[dict[str, str]] = []
        content_start = len(self.content_store.records) if self.content_store else 0

        entries: list[StructuralFile] = []
        for selected in selection.files:
            if selected.language is Language.MARKDOWN:
                continue
            try:
                source = self._read_content(
                    repo=selected.repo,
                    path=selected.path,
                    commit_sha=selected.commit_sha,
                    language=selected.language,
                )
                entry = self.extractor.extract(
                    repo=selected.repo,
                    path=selected.path,
                    commit_sha=selected.commit_sha,
                    source=source,
                    language=selected.language,
                    known_paths=known_paths,
                    blob_sha=selected.blob_sha,
                    source_pr_numbers=selected.source_pr_numbers,
                )
                entries.append(entry)
            except InvalidRecordError as error:
                skipped.append(
                    {
                        "path": selected.path,
                        "reason": error.message,
                        "commit_sha": selected.commit_sha,
                    }
                )
            except OSError as error:
                skipped.append(
                    {
                        "path": selected.path,
                        "reason": f"content_unavailable: {error}",
                        "commit_sha": selected.commit_sha,
                    }
                )

        batch.files = entries
        batch.skipped = skipped

        declared_keys = {
            key.key for entry in entries for key in entry.config_keys
        }
        docs: list[DocMention] = []
        for selected in selection.files:
            if selected.language is not Language.MARKDOWN:
                continue
            try:
                source = self._read_content(
                    repo=selected.repo,
                    path=selected.path,
                    commit_sha=selected.commit_sha,
                    language=selected.language,
                )
            except (InvalidRecordError, OSError) as error:
                skipped.append(
                    {
                        "path": selected.path,
                        "reason": getattr(error, "message", str(error)),
                        "commit_sha": selected.commit_sha,
                    }
                )
                continue
            docs.extend(
                doc_mentions(
                    source,
                    doc_path=selected.path,
                    known_paths=known_paths,
                    known_config_keys=declared_keys,
                )
            )
        batch.docs = docs

        graph = build_structural_graph(batch)
        validation = validate_batch(batch)
        graph_validation = validate_graph(graph)
        validation.errors.extend(graph_validation.errors)
        if not graph_validation.ok:
            validation.ok = False

        write_report: WriteReport | None = None
        if self.writer is not None and validation.ok:
            write_report = self.writer.write(graph, repo=self.config.repo)

        content: list[ContentRecord] = (
            list(self.content_store.records[content_start:])
            if self.content_store is not None
            else []
        )

        return StructuralResult(
            batch=batch,
            graph=graph,
            validation=validation,
            write_report=write_report,
            selection=selection,
            skipped=skipped,
            content=content,
        )

    def run_from_records(
        self,
        *,
        normalized_records: list[dict[str, Any]],
        high_signal_prs: list[dict[str, Any]] | None = None,
    ) -> StructuralResult:
        from app.structural.selection.file_selector import FileSelector

        selector = FileSelector(self.config)
        selection = selector.select(
            normalized_records=normalized_records,
            high_signal_prs=high_signal_prs,
        )
        return self.run(selection)


__all__ = ["StructuralPipeline", "StructuralResult", "ConfigKey"]
