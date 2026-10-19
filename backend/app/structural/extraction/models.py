"""Typed structural extraction models.

Everything here is derived from syntax or explicit text. If a fact cannot
be read from the file, it is absent — never inferred. These models are the
contract between the extractors, the graph-record builder, and the
validator.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Language(StrEnum):
    PYTHON = "python"
    YAML = "yaml"
    TOML = "toml"
    JSON = "json"
    ENV = "env"
    MARKDOWN = "markdown"


class SymbolKind(StrEnum):
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    CONSTANT = "constant"


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
    value_preview: str | None = None
    declared: bool = True


class ConfigReference(_Model):
    key: str
    source_kind: ConfigSourceKind
    path: str
    line: int = Field(ge=1)
    symbol_qualname: str | None = None


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
    target_symbol_id: str
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
