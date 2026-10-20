"""Typed structural extraction models.

Everything here is derived from syntax or explicit text. If a fact cannot
be read from the file, it is absent — never inferred. These models are the
contract between the extractors, the graph-record builder, and the
validator.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


class Language(StrEnum):
    PYTHON = "python"
    YAML = "yaml"
    TOML = "toml"
    JSON = "json"
    ENV = "env"
    MARKDOWN = "markdown"
    # A manually pinned file with no supported parser. Metadata only.
    OTHER = "other"


class SymbolKind(StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    CONSTANT = "constant"
    TYPE = "type"


class ConfigSourceKind(StrEnum):
    YAML = "yaml"
    TOML = "toml"
    JSON = "json"
    ENV_FILE = "env_file"
    ENVIRONMENT = "environment"


class ImportKind(StrEnum):
    ABSOLUTE = "absolute"
    RELATIVE = "relative"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Symbol(_Model):
    symbol_id: str
    name: str
    qualname: str
    kind: SymbolKind
    path: str
    commit_sha: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    parent_qualname: str | None = None
    decorators: list[str] = Field(default_factory=list)
    is_async: bool = False

    @property
    def qualified_name(self) -> str:
        """Module-qualified name, e.g. ``httpx._client.Client.send``.

        ``qualname`` stays hierarchy-relative because that is what ownership
        and reference resolution compare within one file. This property is
        the cross-file, human-facing name.
        """
        from app.structural.extraction.ids import module_name

        module = module_name(self.path)
        return f"{module}.{self.qualname}" if module else self.qualname


class ImportRef(_Model):
    module: str
    kind: ImportKind
    level: int = 0
    names: list[str] = Field(default_factory=list)
    aliases: dict[str, str] = Field(default_factory=dict)
    wildcard: bool = False
    line: int = Field(ge=1)
    resolved_path: str | None = None
    resolved_module: str | None = None


class ConfigKey(_Model):
    key: str
    source_path: str | None = None
    source_kind: ConfigSourceKind
    line: int | None = None
    value: str | None = None
    value_type: str | None = None
    value_preview: str | None = None
    declared: bool = True


class ConfigReference(_Model):
    key: str
    source_kind: ConfigSourceKind
    path: str
    line: int = Field(ge=1)
    symbol_qualname: str | None = None
    # An environment lookup is its own key source. A dotted access points at
    # a key declared elsewhere, so an undeclared target is uncertain.
    kind: Literal["environment", "config"] = "environment"


class CodeRegion(_Model):
    region_id: str
    path: str
    commit_sha: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    kind: str
    symbol_qualname: str | None = None
    config_key: str | None = None


class Reference(_Model):
    source_qualname: str
    # Filled when the target is a symbol in the same parsed file. Imported
    # targets are carried as path+qualname until the batch resolver can see
    # the defining file's symbols.
    target_symbol_id: str | None = None
    target_path: str | None = None
    target_qualname: str | None = None
    name: str
    line: int = Field(ge=1)
    resolution: str


class DocMention(_Model):
    doc_path: str
    target_path: str | None = None
    config_key: str | None = None
    line: int | None = None


class ExternalRef(_Model):
    module: str
    kind: str = "package"
    name: str | None = None


class StructuralFile(_Model):
    repo: str
    path: str
    commit_sha: str
    language: Language
    blob_sha: str | None = None
    source_pr_numbers: list[int] = Field(default_factory=list)
    content_hash: str | None = None
    byte_size: int | None = None
    line_count: int | None = None
    symbols: list[Symbol] = Field(default_factory=list)
    imports: list[ImportRef] = Field(default_factory=list)
    config_keys: list[ConfigKey] = Field(default_factory=list)
    config_references: list[ConfigReference] = Field(default_factory=list)
    regions: list[CodeRegion] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    external_refs: list[ExternalRef] = Field(default_factory=list)
    parse_error: str | None = None

    @property
    def file_id(self) -> str:
        from app.structural.extraction.ids import file_id

        return file_id(repo=self.repo, path=self.path, commit_sha=self.commit_sha)


class StructuralBatch(_Model):
    repo: str
    commit_sha: str | None = None
    files: list[StructuralFile] = Field(default_factory=list)
    docs: list[DocMention] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser output contract
#
# The models below are the language-agnostic contract between per-language
# parsers and everything downstream. They are deliberately flat and carry
# resolved IDs instead of paths: a parser either knows the identity of what it
# found, or it does not emit it. The graph-facing models above remain the
# serialization contract until extractors migrate to this output shape.
# ---------------------------------------------------------------------------

ExtractedSymbolKind = Literal[
    "function", "method", "class", "constant", "variable", "type"
]

ImportResolutionStatus = Literal[
    "resolved_internal", "resolved_external", "unresolved"
]

CodeRegionSource = Literal[
    "definition", "changed_hunk", "review_comment", "manual"
]


def _require_text(value: str, field_name: str | None) -> str:
    if not value.strip():
        raise ValueError(f"{field_name or 'value'} must be non-empty")
    return value


def _require_optional_text(
    value: str | None, field_name: str | None
) -> str | None:
    if value is not None and not value.strip():
        raise ValueError(f"{field_name or 'value'} must be non-empty when present")
    return value


class ExtractedSymbol(_Model):
    """A definition found by a parser, scoped to one file version."""

    symbol_id: str
    file_id: str
    name: str
    qualified_name: str
    kind: ExtractedSymbolKind
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    signature: str | None = None
    docstring: str | None = None
    parent_symbol_id: str | None = None

    @field_validator("symbol_id", "file_id", "name", "qualified_name")
    @classmethod
    def _names_are_present(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("parent_symbol_id")
    @classmethod
    def _parent_id_is_present(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        return _require_optional_text(value, info.field_name)

    @model_validator(mode="after")
    def _line_bounds(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


class ExtractedImport(_Model):
    """One imported name or module, with resolution stated explicitly.

    ``resolved_internal`` is the only status that may carry a
    ``resolved_file_id``. Conversely, an internal resolution without a target
    is not an internal resolution. Relative imports can never resolve to an
    external package: if the target is not in the selected set they are
    unresolved, never guessed (D-0015).
    """

    import_id: str
    file_id: str
    module: str | None
    name: str | None
    alias: str | None
    is_relative: bool
    resolved_file_id: str | None = None
    resolution_status: ImportResolutionStatus

    @field_validator("import_id", "file_id")
    @classmethod
    def _ids_are_present(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("module", "name", "alias", "resolved_file_id")
    @classmethod
    def _optional_text_is_present(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        return _require_optional_text(value, info.field_name)

    @model_validator(mode="after")
    def _resolution_is_honest(self) -> Self:
        if self.module is None and self.name is None:
            raise ValueError("an import must name a module, a symbol, or both")
        if self.resolution_status == "resolved_internal":
            if self.resolved_file_id is None:
                raise ValueError("resolved_internal requires resolved_file_id")
        elif self.resolved_file_id is not None:
            raise ValueError(
                f"{self.resolution_status} must not carry resolved_file_id"
            )
        if self.is_relative and self.resolution_status == "resolved_external":
            raise ValueError("a relative import cannot resolve to an external package")
        return self


class ExtractedConfigKey(_Model):
    """A declared configuration key with its literal value and type."""

    config_id: str
    file_id: str
    key: str
    value: str | None
    value_type: str | None
    line: int = Field(ge=1)

    @field_validator("config_id", "file_id", "key")
    @classmethod
    def _ids_are_present(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("value_type")
    @classmethod
    def _value_type_is_present(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        return _require_optional_text(value, info.field_name)


class ExtractedCodeRegion(_Model):
    """A bounded span of file content, with where the span came from.

    ``excerpt`` is optional because content already lives in the blob store;
    the ``content_hash`` is what makes the span verifiable without copying
    source into the graph (D-0020/D-0021).
    """

    region_id: str
    file_id: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    content_hash: str
    excerpt: str | None
    symbol_id: str | None = None
    source: CodeRegionSource

    @field_validator("region_id", "file_id", "content_hash")
    @classmethod
    def _ids_are_present(cls, value: str, info: ValidationInfo) -> str:
        return _require_text(value, info.field_name)

    @field_validator("symbol_id")
    @classmethod
    def _symbol_id_is_present(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        return _require_optional_text(value, info.field_name)

    @model_validator(mode="after")
    def _line_bounds(self) -> Self:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self


__all__ = [
    "CodeRegionSource",
    "ExtractedCodeRegion",
    "ExtractedConfigKey",
    "ExtractedImport",
    "ExtractedSymbol",
    "ExtractedSymbolKind",
    "ImportResolutionStatus",
]
