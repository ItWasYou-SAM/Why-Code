"""Symbol extraction contracts: names, hierarchy, bounds, kinds."""

from structural_helpers import COMMIT, REPO, fixture_bytes

from app.structural.extraction.models import Language, SymbolKind
from app.structural.extraction.symbol_extractor import extract_symbols
from app.structural.languages.registry import LanguageRegistry


def symbols():
    registry = LanguageRegistry()
    source = fixture_bytes("sample_client.py")
    tree = registry.parse(Language.PYTHON, source)
    return extract_symbols(
        tree, source, repo=REPO, path="httpx/_client.py", commit_sha=COMMIT
    )


def by_qualname():
    return {symbol.qualname: symbol for symbol in symbols()}


class TestSymbols:
    def test_class_function_and_methods_are_found(self) -> None:
        found = by_qualname()
        assert found["Client"].kind is SymbolKind.CLASS
        assert found["Client.send"].kind is SymbolKind.METHOD
        assert found["Client._send"].kind is SymbolKind.METHOD
        assert found["build_client"].kind is SymbolKind.FUNCTION

    def test_constants_are_found_with_module_and_class_scope(self) -> None:
        found = by_qualname()
        assert found["DEFAULT_RETRIES"].kind is SymbolKind.CONSTANT
        assert found["RETRY_STATUS_CODES"].kind is SymbolKind.CONSTANT
        assert found["Client.TIMEOUT_KEY"].kind is SymbolKind.CONSTANT

    def test_async_is_recorded(self) -> None:
        assert by_qualname()["Client._send"].is_async is True
        assert by_qualname()["Client.send"].is_async is False

    def test_decorators_are_recorded(self) -> None:
        assert by_qualname()["Client.is_retryable"].decorators == ["property"]

    def test_line_bounds_are_one_based_and_ordered(self) -> None:
        for symbol in symbols():
            assert symbol.start_line >= 1
            assert symbol.end_line >= symbol.start_line
            assert symbol.end_line <= fixture_bytes("sample_client.py").count(b"\n") + 1

    def test_symbol_ids_are_path_and_commit_scoped(self) -> None:
        client = by_qualname()["Client"]
        assert client.symbol_id == f"httpx:symbol:httpx/_client.py@{COMMIT}:Client"

    def test_parent_qualname_matches_hierarchy(self) -> None:
        found = by_qualname()
        assert found["Client.send"].parent_qualname == "Client"
        assert found["build_client"].parent_qualname is None

    def test_docstrings_are_captured_from_the_definition_body(self) -> None:
        found = by_qualname()
        assert found["Client"].docstring == "A minimal client."
        assert found["Client.send"].docstring is None

    def test_no_duplicate_symbol_ids(self) -> None:
        ids = [symbol.symbol_id for symbol in symbols()]
        assert len(ids) == len(set(ids))

    def test_source_with_no_symbols_yields_empty_list(self) -> None:
        from app.structural.extraction.models import Language
        from app.structural.languages.registry import LanguageRegistry

        tree = LanguageRegistry().parse(Language.PYTHON, b"x = 1\n")
        assert extract_symbols(
            tree, b"x = 1\n", repo=REPO, path="a.py", commit_sha=COMMIT
        ) == []
