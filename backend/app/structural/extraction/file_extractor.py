"""Per-file structural extraction.

One function, one file, one commit. The extractor is total: it either
returns a ``StructuralFile`` or raises ``InvalidRecordError``. A file that
cannot be parsed produces no partial facts, because a half-parsed symbol
table is worse than a logged skip.
"""

from __future__ import annotations

from typing import Any

from app.core.errors import InvalidRecordError
from app.structural.content.metrics import content_sha256, count_lines, utf8_size
from app.structural.extraction.config_extractor import (
    extract_config_keys,
    extract_config_references,
)
from app.structural.extraction.doc_extractor import extract_doc_mentions
from app.structural.extraction.import_extractor import extract_imports
from app.structural.extraction.models import (
    ConfigReference,
    DocMention,
    ExternalRef,
    Language,
    StructuralFile,
)
from app.structural.extraction.reference_extractor import extract_references
from app.structural.extraction.region_extractor import (
    regions_for_config_keys,
    regions_for_symbols,
)
from app.structural.extraction.symbol_extractor import extract_symbols
from app.structural.languages.registry import LanguageRegistry
from app.structural.languages.router import ExtractionHandler, handler_for_language


class FileExtractor:
    def __init__(self, registry: LanguageRegistry | None = None) -> None:
        self.registry = registry or LanguageRegistry()

    def extract(
        self,
        *,
        repo: str,
        path: str,
        commit_sha: str,
        source: str,
        language: Language,
        known_paths: set[str] | None = None,
        blob_sha: str | None = None,
        source_pr_numbers: list[int] | None = None,
    ) -> StructuralFile:
        encoded = source.encode("utf-8")
        result = StructuralFile(
            repo=repo,
            path=path,
            commit_sha=commit_sha,
            language=language,
            blob_sha=blob_sha,
            source_pr_numbers=source_pr_numbers or [],
            content_hash=content_sha256(source),
            byte_size=utf8_size(source),
            line_count=count_lines(source),
        )

        handler = handler_for_language(language)

        # Markdown and metadata-only files keep their File-level facts but
        # contribute no symbols, imports, or regions.
        if handler in {ExtractionHandler.MARKDOWN, ExtractionHandler.METADATA}:
            return result

        if handler is ExtractionHandler.CONFIG:
            keys = extract_config_keys(source, path=path, language=language)
            result.config_keys = keys
            result.regions = regions_for_config_keys(
                keys, repo=repo, path=path, commit_sha=commit_sha
            )
            return result

        if handler is not ExtractionHandler.PYTHON:
            raise InvalidRecordError(
                f"unsupported language {language}", component="structural"
            )

        tree = self.registry.parse(language, encoded)
        symbols = extract_symbols(
            tree, encoded, repo=repo, path=path, commit_sha=commit_sha
        )
        imports = extract_imports(tree, encoded, path=path, known_paths=known_paths)
        references = extract_references(
            tree, encoded, symbols=symbols, imports=imports, path=path
        )
        config_references = extract_config_references(
            source,
            path=path,
            symbol_ranges=[
                (symbol.qualname, symbol.start_line, symbol.end_line)
                for symbol in symbols
            ],
        )

        result.symbols = symbols
        result.imports = imports
        result.references = references
        result.config_references = config_references
        result.regions = regions_for_symbols(
            symbols, repo=repo, path=path, commit_sha=commit_sha
        )
        result.external_refs = _external_refs(imports, path=path)
        return result


def _external_refs(imports: list[Any], *, path: str) -> list[ExternalRef]:
    refs: list[ExternalRef] = []
    seen: set[str] = set()
    for imported in imports:
        if imported.resolved_path is not None:
            continue
        module = imported.module or ""
        if not module:
            continue
        root = module.split(".")[0]
        if not root or root == path.split("/")[0].replace(".py", ""):
            continue
        if root in seen:
            continue
        seen.add(root)
        refs.append(ExternalRef(module=root))
    return refs


def extract_file(
    *,
    repo: str,
    path: str,
    commit_sha: str,
    source: str,
    language: Language,
    known_paths: set[str] | None = None,
    blob_sha: str | None = None,
    source_pr_numbers: list[int] | None = None,
    registry: LanguageRegistry | None = None,
) -> StructuralFile:
    return FileExtractor(registry).extract(
        repo=repo,
        path=path,
        commit_sha=commit_sha,
        source=source,
        language=language,
        known_paths=known_paths,
        blob_sha=blob_sha,
        source_pr_numbers=source_pr_numbers,
    )


def doc_mentions(
    source: str,
    *,
    doc_path: str,
    known_paths: set[str] | None = None,
    known_config_keys: set[str] | None = None,
) -> list[DocMention]:
    return extract_doc_mentions(
        source,
        doc_path=doc_path,
        known_paths=known_paths,
        known_config_keys=known_config_keys,
    )


__all__ = ["FileExtractor", "extract_file", "doc_mentions", "ConfigReference"]
