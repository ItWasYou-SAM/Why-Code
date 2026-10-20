"""Phase 5 contracts: the parser output models.

These models are the language-agnostic boundary between parsers and the
graph. The tests pin the two things that must not drift:

* identity fields are present and line spans are ordered;
* import resolution states carry exactly the information they claim to.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError
from structural_helpers import COMMIT, REPO

from app.structural.extraction.ids import (
    config_key_id,
    file_id,
    region_id,
    symbol_id,
)
from app.structural.extraction.models import (
    ExtractedCodeRegion,
    ExtractedConfigKey,
    ExtractedImport,
    ExtractedSymbol,
)

PATH = "httpx/_client.py"
FILE_ID = file_id(repo=REPO, path=PATH, commit_sha=COMMIT)


def make_symbol(**overrides: Any) -> ExtractedSymbol:
    values: dict[str, Any] = {
        "symbol_id": symbol_id(
            repo=REPO, path=PATH, commit_sha=COMMIT, qualname="Client.send"
        ),
        "file_id": FILE_ID,
        "name": "send",
        "qualified_name": "Client.send",
        "kind": "method",
        "start_line": 10,
        "end_line": 20,
        "signature": "def send(self, request: Request)",
        "docstring": "Send a request.",
        "parent_symbol_id": symbol_id(
            repo=REPO, path=PATH, commit_sha=COMMIT, qualname="Client"
        ),
    }
    return ExtractedSymbol(**{**values, **overrides})


def make_import(**overrides: Any) -> ExtractedImport:
    values: dict[str, Any] = {
        "import_id": f"{FILE_ID}:import:1",
        "file_id": FILE_ID,
        "module": "httpx._config",
        "name": "Timeout",
        "alias": None,
        "is_relative": False,
        "resolved_file_id": file_id(
            repo=REPO, path="httpx/_config.py", commit_sha=COMMIT
        ),
        "resolution_status": "resolved_internal",
    }
    return ExtractedImport(**{**values, **overrides})


def make_config_key(**overrides: Any) -> ExtractedConfigKey:
    values: dict[str, Any] = {
        "config_id": config_key_id(repo=REPO, source="config.yaml", key="default.timeout"),
        "file_id": file_id(repo=REPO, path="config.yaml", commit_sha=COMMIT),
        "key": "default.timeout",
        "value": "5.0",
        "value_type": "float",
        "line": 3,
    }
    return ExtractedConfigKey(**{**values, **overrides})


def make_region(**overrides: Any) -> ExtractedCodeRegion:
    values: dict[str, Any] = {
        "region_id": region_id(
            repo=REPO, path=PATH, commit_sha=COMMIT, start_line=10, end_line=20
        ),
        "file_id": FILE_ID,
        "start_line": 10,
        "end_line": 20,
        "content_hash": "a" * 64,
        "excerpt": "def send(self, request): ...",
        "symbol_id": make_symbol().symbol_id,
        "source": "definition",
    }
    return ExtractedCodeRegion(**{**values, **overrides})


class TestExtractedSymbol:
    @pytest.mark.parametrize(
        "kind", ["function", "method", "class", "constant", "variable", "type"]
    )
    def test_every_declared_kind_is_accepted(self, kind: str) -> None:
        assert make_symbol(kind=kind).kind == kind

    def test_optional_declaration_facts_are_kept(self) -> None:
        symbol = make_symbol()
        assert symbol.signature == "def send(self, request: Request)"
        assert symbol.docstring == "Send a request."
        assert symbol.parent_symbol_id is not None

    def test_optional_facts_default_to_absent(self) -> None:
        symbol = make_symbol(
            signature=None, docstring=None, parent_symbol_id=None
        )
        assert symbol.signature is None
        assert symbol.docstring is None
        assert symbol.parent_symbol_id is None

    def test_end_line_must_not_precede_start_line(self) -> None:
        with pytest.raises(ValidationError, match="end_line"):
            make_symbol(start_line=20, end_line=10)

    @pytest.mark.parametrize("field", ["symbol_id", "file_id", "name", "qualified_name"])
    def test_identity_and_names_cannot_be_blank(self, field: str) -> None:
        with pytest.raises(ValidationError):
            make_symbol(**{field: "   "})

    def test_unknown_kind_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_symbol(kind="module")


class TestExtractedImport:
    @pytest.mark.parametrize(
        "status", ["resolved_internal", "resolved_external", "unresolved"]
    )
    def test_declared_resolution_statuses_are_accepted(self, status: str) -> None:
        resolved = (
            make_import().resolved_file_id
            if status == "resolved_internal"
            else None
        )
        imported = make_import(
            resolution_status=status, resolved_file_id=resolved
        )
        assert imported.resolution_status == status

    def test_internal_resolution_requires_a_target_file(self) -> None:
        with pytest.raises(ValidationError, match="resolved_file_id"):
            make_import(resolved_file_id=None)

    @pytest.mark.parametrize("status", ["resolved_external", "unresolved"])
    def test_non_internal_resolution_forbids_a_target_file(self, status: str) -> None:
        with pytest.raises(ValidationError, match="must not carry"):
            make_import(
                resolution_status=status,
                resolved_file_id=file_id(
                    repo=REPO, path="httpx/_config.py", commit_sha=COMMIT
                ),
            )

    def test_relative_import_cannot_resolve_external(self) -> None:
        with pytest.raises(ValidationError, match="relative import"):
            make_import(
                is_relative=True,
                resolution_status="resolved_external",
                resolved_file_id=None,
            )

    def test_relative_import_without_a_target_stays_unresolved(self) -> None:
        imported = make_import(
            module=None,
            name="_types",
            is_relative=True,
            resolution_status="unresolved",
            resolved_file_id=None,
        )
        assert imported.resolution_status == "unresolved"

    def test_import_must_name_something(self) -> None:
        with pytest.raises(ValidationError, match="must name"):
            make_import(module=None, name=None)

    def test_module_only_import_is_valid(self) -> None:
        imported = make_import(
            module="os",
            name=None,
            alias="system",
            resolution_status="resolved_external",
            resolved_file_id=None,
        )
        assert imported.alias == "system"

    def test_empty_optional_fields_are_rejected_when_present(self) -> None:
        with pytest.raises(ValidationError):
            make_import(name="")


class TestExtractedConfigKey:
    def test_literal_value_and_type_are_kept(self) -> None:
        key = make_config_key()
        assert (key.value, key.value_type) == ("5.0", "float")

    def test_empty_value_is_still_a_literal(self) -> None:
        key = make_config_key(value="", value_type="string")
        assert key.value == ""

    def test_line_is_one_based(self) -> None:
        with pytest.raises(ValidationError):
            make_config_key(line=0)

    @pytest.mark.parametrize("field", ["config_id", "file_id", "key"])
    def test_identity_and_key_cannot_be_blank(self, field: str) -> None:
        with pytest.raises(ValidationError):
            make_config_key(**{field: " "})


class TestExtractedCodeRegion:
    @pytest.mark.parametrize(
        "source", ["definition", "changed_hunk", "review_comment", "manual"]
    )
    def test_every_provenance_source_is_accepted(self, source: str) -> None:
        assert make_region(source=source).source == source

    def test_excerpt_and_symbol_are_optional(self) -> None:
        region = make_region(excerpt=None, symbol_id=None)
        assert region.excerpt is None
        assert region.symbol_id is None

    def test_end_line_must_not_precede_start_line(self) -> None:
        with pytest.raises(ValidationError, match="end_line"):
            make_region(start_line=20, end_line=10)

    def test_content_hash_is_required(self) -> None:
        with pytest.raises(ValidationError):
            make_region(content_hash="")

    def test_unknown_source_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_region(source="inferred")


class TestModelHygiene:
    def test_unknown_fields_are_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_symbol(language="python")

    def test_outputs_serialize_to_json_ready_dicts(self) -> None:
        for model in (
            make_symbol(),
            make_import(),
            make_config_key(),
            make_region(),
        ):
            payload = model.model_dump(mode="json")
            assert payload["file_id"] == model.file_id
            assert isinstance(payload, dict)
