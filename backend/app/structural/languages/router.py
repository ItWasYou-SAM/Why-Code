"""Language routing: one suffix table, one decision per file.

Phase 3 deliberately supports a very small set of file types. Python is the
only language parsed with Tree-sitter; YAML and TOML contribute config keys;
Markdown contributes a Doc node. Everything else is skipped unless the caller
pinned the path explicitly, in which case we keep lightweight metadata (hash,
size, line count) and parse nothing.

Keeping the decision here means selection, extraction, and the CLI cannot
disagree about what a ``.pyi`` or a ``.json`` file is.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from app.structural.extraction.models import Language


class LanguageKind(StrEnum):
    """The exact suffix table we promise to support for the hackathon."""

    PYTHON = "python"
    YAML_CONFIG = "yaml_config"
    TOML_CONFIG = "toml_config"
    MARKDOWN = "markdown"
    METADATA_ONLY = "metadata_only"
    UNSUPPORTED = "unsupported"


class ExtractionHandler(StrEnum):
    """What the extractor should do with a routed file."""

    PYTHON = "python"
    CONFIG = "config"
    MARKDOWN = "markdown"
    METADATA = "metadata"
    SKIP = "skip"


LANGUAGE_REGISTRY: dict[str, LanguageKind] = {
    ".py": LanguageKind.PYTHON,
    ".pyi": LanguageKind.PYTHON,
    ".yaml": LanguageKind.YAML_CONFIG,
    ".yml": LanguageKind.YAML_CONFIG,
    ".toml": LanguageKind.TOML_CONFIG,
    ".md": LanguageKind.MARKDOWN,
}


LANGUAGE_BY_KIND: dict[LanguageKind, Language] = {
    LanguageKind.PYTHON: Language.PYTHON,
    LanguageKind.YAML_CONFIG: Language.YAML,
    LanguageKind.TOML_CONFIG: Language.TOML,
    LanguageKind.MARKDOWN: Language.MARKDOWN,
    LanguageKind.METADATA_ONLY: Language.OTHER,
}

_HANDLER_BY_KIND: dict[LanguageKind, ExtractionHandler] = {
    LanguageKind.PYTHON: ExtractionHandler.PYTHON,
    LanguageKind.YAML_CONFIG: ExtractionHandler.CONFIG,
    LanguageKind.TOML_CONFIG: ExtractionHandler.CONFIG,
    LanguageKind.MARKDOWN: ExtractionHandler.MARKDOWN,
    LanguageKind.METADATA_ONLY: ExtractionHandler.METADATA,
    LanguageKind.UNSUPPORTED: ExtractionHandler.SKIP,
}


@dataclass(frozen=True)
class LanguageRoute:
    path: str
    suffix: str
    kind: LanguageKind
    language: Language | None
    supported: bool
    pinned: bool = False

    @property
    def handler(self) -> ExtractionHandler:
        return _HANDLER_BY_KIND[self.kind]


def route_path(path: str, *, pinned: bool = False) -> LanguageRoute:
    """Route one path. Unknown suffixes are skipped unless explicitly pinned.

    A pinned unknown file is not guessed at: it becomes an ``OTHER`` file with
    metadata only. That keeps a deliberate inclusion (README.rst, a JS file
    the user asked for) in the graph without pretending we can parse it.
    """
    suffix = PurePosixPath(path).suffix.lower()
    kind = LANGUAGE_REGISTRY.get(suffix)
    if kind is not None:
        return LanguageRoute(
            path=path,
            suffix=suffix,
            kind=kind,
            language=LANGUAGE_BY_KIND[kind],
            supported=True,
            pinned=pinned,
        )
    if pinned:
        return LanguageRoute(
            path=path,
            suffix=suffix,
            kind=LanguageKind.METADATA_ONLY,
            language=Language.OTHER,
            supported=True,
            pinned=True,
        )
    return LanguageRoute(
        path=path,
        suffix=suffix,
        kind=LanguageKind.UNSUPPORTED,
        language=None,
        supported=False,
    )


def handler_for_language(language: Language) -> ExtractionHandler:
    """Map a typed language to its extraction behavior.

    JSON and ``.env`` are not routable (see ``LANGUAGE_REGISTRY``), but the
    config extractor still knows how to read them. Direct callers and old
    batches keep working; the selector will never produce them.
    """
    if language is Language.PYTHON:
        return ExtractionHandler.PYTHON
    if language in {Language.YAML, Language.TOML, Language.JSON, Language.ENV}:
        return ExtractionHandler.CONFIG
    if language is Language.MARKDOWN:
        return ExtractionHandler.MARKDOWN
    return ExtractionHandler.METADATA


def language_for_path(path: str) -> Language | None:
    """Supported language for a path, or ``None`` when it is skipped."""
    return route_path(path).language


__all__ = [
    "ExtractionHandler",
    "LANGUAGE_BY_KIND",
    "LANGUAGE_REGISTRY",
    "LanguageKind",
    "LanguageRoute",
    "handler_for_language",
    "language_for_path",
    "route_path",
]
