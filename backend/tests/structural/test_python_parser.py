"""Parser + language registry contracts."""

import pytest
from structural_helpers import fixture_bytes

from app.core.errors import InvalidRecordError
from app.structural.extraction.models import Language
from app.structural.languages.registry import LanguageRegistry


class TestRegistry:
    def test_python_is_supported_and_yaml_is_not_a_parser(self) -> None:
        registry = LanguageRegistry()
        assert registry.supports(Language.PYTHON)
        assert not registry.supports(Language.YAML)

    def test_unknown_language_raises(self) -> None:
        registry = LanguageRegistry()
        with pytest.raises(InvalidRecordError):
            registry.parser_for(Language.TOML)

    def test_parses_a_valid_python_file(self) -> None:
        registry = LanguageRegistry()
        tree = registry.parse(Language.PYTHON, fixture_bytes("sample_client.py"))
        assert tree.root_node.type == "module"
        assert not tree.root_node.has_error

    def test_syntax_error_is_rejected_not_half_parsed(self) -> None:
        registry = LanguageRegistry()
        with pytest.raises(InvalidRecordError) as error:
            registry.parse(Language.PYTHON, b"def broken(:\n    pass\n")
        assert "syntax" in str(error.value)

    def test_queries_are_loadable(self) -> None:
        from app.structural.languages.python_parser import load_query

        for name in (
            "python_definitions.scm",
            "python_imports.scm",
            "python_references.scm",
        ):
            assert load_query(name).strip()
