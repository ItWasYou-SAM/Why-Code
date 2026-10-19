"""Phase 4 contracts: query-driven extraction and honest references.

Definitions, imports, and references all come from named Tree-sitter
queries with explicit captures. A REFERENCES edge exists only when the
target is provably a same-file symbol or an imported symbol whose defining
file is in the batch.
"""

from __future__ import annotations

from structural_helpers import COMMIT, REPO

from app.structural.extraction.file_extractor import extract_file
from app.structural.extraction.import_extractor import extract_imports
from app.structural.extraction.models import Language, StructuralBatch
from app.structural.extraction.reference_extractor import (
    extract_references,
    resolve_imported_references,
)
from app.structural.extraction.symbol_extractor import extract_symbols
from app.structural.graph.graph_records import build_structural_graph
from app.structural.languages.python_parser import load_query, run_query
from app.structural.languages.registry import LanguageRegistry
from app.structural.validation.graph_validator import validate_graph

A_PATH = "pkg/a.py"
B_PATH = "pkg/b.py"
KNOWN = {A_PATH, B_PATH, "pkg/__init__.py"}


def parse(source: str):
    encoded = source.encode("utf-8")
    tree = LanguageRegistry().parse(Language.PYTHON, encoded)
    return tree, encoded


def references(source: str, *, known: set[str] | None = None):
    tree, encoded = parse(source)
    symbols = extract_symbols(tree, encoded, repo=REPO, path=A_PATH, commit_sha=COMMIT)
    imports = extract_imports(tree, encoded, path=A_PATH, known_paths=known or set())
    return extract_references(
        tree, encoded, symbols=symbols, imports=imports, path=A_PATH
    )


class TestQueries:
    def test_definition_query_uses_navigation_captures(self) -> None:
        query = load_query("python_definitions.scm")
        for capture in (
            "@definition.function",
            "@definition.class",
            "@name",
            "@parameters",
            "@body",
        ):
            assert capture in query

    def test_import_query_captures_module_names_and_aliases(self) -> None:
        query = load_query("python_imports.scm")
        for capture in (
            "@import.statement",
            "@import.from_statement",
            "@import.name",
            "@import.module",
            "@import.relative_module",
        ):
            assert capture in query

    def test_reference_query_captures_calls_and_attributes(self) -> None:
        query = load_query("python_references.scm")
        for capture in (
            "@reference.call",
            "@reference.attribute_call",
            "@reference.attribute",
            "@reference.name",
            "@reference.object",
        ):
            assert capture in query

    def test_run_query_keeps_a_definition_with_its_name(self) -> None:
        tree, encoded = parse("def outer():\n    return 1\n")
        matches = run_query("python_definitions.scm", tree.root_node)
        found = {
            encoded[node.start_byte : node.end_byte]
            for _pattern, captures in matches
            for key, nodes in captures.items()
            if key == "definition.function"
            for node in nodes
        }
        assert found == {b"def outer():\n    return 1"}


class TestReferenceRules:
    def test_same_file_symbol_is_referenced(self) -> None:
        found = references("def helper():\n    return 1\n\n\ndef use():\n    return helper()\n")
        assert [(ref.name, ref.resolution) for ref in found] == [("helper", "same_file")]

    def test_imported_symbol_carries_the_defining_path(self) -> None:
        found = references(
            "from pkg.b import helper\n\n\ndef use():\n    return helper()\n",
            known=KNOWN,
        )
        assert [(ref.name, ref.target_path, ref.target_qualname) for ref in found] == [
            ("helper", B_PATH, "helper")
        ]

    def test_attribute_on_imported_module_is_referenced(self) -> None:
        found = references(
            "import pkg.b\n\n\ndef use():\n    return pkg.b.helper()\n",
            known=KNOWN,
        )
        assert [(ref.target_path, ref.target_qualname) for ref in found] == [
            (B_PATH, "helper")
        ]

    def test_attribute_on_imported_class_is_referenced(self) -> None:
        found = references(
            "from pkg.b import Client\n\n\ndef use():\n    return Client.parse()\n",
            known=KNOWN,
        )
        assert [(ref.target_path, ref.target_qualname) for ref in found] == [
            (B_PATH, "Client.parse")
        ]

    def test_self_method_call_is_a_local_reference(self) -> None:
        found = references(
            "class Service:\n"
            "    def send(self):\n"
            "        return self._send()\n\n"
            "    def _send(self):\n"
            "        return 1\n"
        )
        assert [(ref.name, ref.resolution) for ref in found] == [("_send", "same_file")]

    def test_unresolvable_external_access_is_discarded(self) -> None:
        found = references(
            "import requests\n\n\ndef use():\n    return requests.get('x')\n"
        )
        assert found == []

    def test_parent_of_an_imported_submodule_is_not_guessed(self) -> None:
        # ``import pkg.b`` binds ``pkg``; a call on ``pkg`` alone does not
        # prove the target lives in ``pkg/b.py``.
        found = references(
            "import pkg.b\n\n\ndef use():\n    return pkg.helper()\n",
            known=KNOWN,
        )
        assert found == []

    def test_builtin_calls_are_not_references(self) -> None:
        found = references("def use():\n    return len([1])\n")
        assert found == []


class TestBatchResolution:
    def _entries(self):
        a = extract_file(
            repo=REPO,
            path=A_PATH,
            commit_sha=COMMIT,
            source="from pkg.b import helper\n\n\ndef use():\n    return helper()\n",
            language=Language.PYTHON,
            known_paths=KNOWN,
        )
        b = extract_file(
            repo=REPO,
            path=B_PATH,
            commit_sha=COMMIT,
            source="def helper():\n    return 1\n",
            language=Language.PYTHON,
            known_paths=KNOWN,
        )
        return a, b

    def test_imported_reference_resolves_to_the_other_file_symbol(self) -> None:
        a, b = self._entries()
        resolve_imported_references([a, b])
        assert a.references[0].target_symbol_id == b.symbols[0].symbol_id
        assert a.references[0].resolution == "imported"

    def test_resolved_reference_becomes_a_graph_edge(self) -> None:
        a, b = self._entries()
        resolve_imported_references([a, b])
        graph = build_structural_graph(StructuralBatch(repo=REPO, files=[a, b]))
        edges = [edge for edge in graph.edges if edge.rel_type == "REFERENCES"]
        assert [edge.to_id for edge in edges] == [b.symbols[0].symbol_id]
        assert validate_graph(graph).ok, validate_graph(graph).errors

    def test_unresolved_imported_reference_is_dropped_not_guessed(self) -> None:
        a = extract_file(
            repo=REPO,
            path=A_PATH,
            commit_sha=COMMIT,
            source="from pkg.b import ghost\n\n\ndef use():\n    return ghost()\n",
            language=Language.PYTHON,
            known_paths=KNOWN,
        )
        resolve_imported_references([a])
        assert a.references[0].target_path == B_PATH
        assert a.references[0].target_symbol_id is None
        graph = build_structural_graph(StructuralBatch(repo=REPO, files=[a]))
        assert not [edge for edge in graph.edges if edge.rel_type == "REFERENCES"]
        assert validate_graph(graph).ok, validate_graph(graph).errors
