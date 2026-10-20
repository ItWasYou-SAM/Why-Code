"""Phase 6 contracts: what is a definition, an import, and a config use.

The rules are deliberately narrow. A name becomes a symbol only when the
syntax proves a definition at module or class scope; an import becomes a file
edge only when the target is in the selected path set; a config edge exists
only when a declared key appears literally in code.
"""

from __future__ import annotations

from structural_helpers import COMMIT, REPO

from app.structural.extraction.config_extractor import (
    extract_config_keys,
    extract_config_references,
)
from app.structural.extraction.file_extractor import extract_file
from app.structural.extraction.models import Language, StructuralBatch, SymbolKind
from app.structural.extraction.outputs import build_extraction_outputs
from app.structural.graph.graph_records import build_structural_graph


def extract(
    source: str,
    *,
    path: str = "pkg/mod.py",
    known_paths: set[str] | None = None,
):
    return extract_file(
        repo=REPO,
        path=path,
        commit_sha=COMMIT,
        source=source,
        language=Language.PYTHON,
        known_paths=known_paths or set(),
    )


def symbols_by_qualname(source: str, *, path: str = "pkg/mod.py"):
    return {symbol.qualname: symbol for symbol in extract(source, path=path).symbols}


class TestSymbolRules:
    def test_top_level_function_class_method_and_constant_are_extracted(self) -> None:
        found = symbols_by_qualname(
            "DEFAULT_RETRIES = 0\n\n"
            "def build() -> int:\n"
            "    return DEFAULT_RETRIES\n\n"
            "class Client:\n"
            "    TIMEOUT = 5\n"
            "    def send(self) -> int:\n"
            "        return self.TIMEOUT\n"
        )
        assert found["build"].kind is SymbolKind.FUNCTION
        assert found["Client"].kind is SymbolKind.CLASS
        assert found["Client.send"].kind is SymbolKind.METHOD
        assert found["DEFAULT_RETRIES"].kind is SymbolKind.CONSTANT
        assert found["Client.TIMEOUT"].kind is SymbolKind.CONSTANT

    def test_explicit_and_simple_type_aliases_are_extracted(self) -> None:
        found = symbols_by_qualname(
            "type UserId = int\n"
            "HTTPStatus = int\n"
            "MaybeInt = int | None\n"
            "Names = list[str]\n"
            "Node = 'Node'\n"
            "lowercase = int\n"
            "T = TypeVar('T')\n"
        )
        for name in ("UserId", "HTTPStatus", "MaybeInt", "Names", "Node"):
            assert found[name].kind is SymbolKind.TYPE
        assert "lowercase" not in found
        assert "T" not in found

    def test_qualified_names_include_the_module_path(self) -> None:
        entry = extract(
            "class HTTPTransport:\n"
            "    def handle_request(self) -> None:\n"
            "        pass\n",
            path="httpx/_transports/default.py",
        )
        by_name = {symbol.name: symbol for symbol in entry.symbols}
        assert (
            by_name["handle_request"].qualified_name
            == "httpx._transports.default.HTTPTransport.handle_request"
        )
        assert (
            by_name["HTTPTransport"].qualified_name
            == "httpx._transports.default.HTTPTransport"
        )

    def test_nested_functions_get_a_conservative_local_qualname(self) -> None:
        found = symbols_by_qualname(
            "def outer() -> None:\n"
            "    def inner() -> None:\n"
            "        pass\n"
        )
        assert found["outer.inner"].qualname == "outer.inner"
        assert found["outer.inner"].qualified_name == "pkg.mod.outer.inner"
        ids = [symbol.symbol_id for symbol in found.values()]
        assert len(ids) == len(set(ids))

    def test_local_variables_and_local_aliases_are_not_symbols(self) -> None:
        found = symbols_by_qualname(
            "def build() -> None:\n"
            "    LOCAL = 1\n"
            "    type LocalAlias = int\n"
            "    other = int\n"
        )
        assert set(found) == {"build"}

    def test_lambdas_are_not_symbols(self) -> None:
        found = symbols_by_qualname("HANDLER = lambda value: value\n")
        assert found == {}

    def test_comprehension_variables_are_not_symbols(self) -> None:
        found = symbols_by_qualname("VALUES = [item for item in range(3)]\n")
        assert "item" not in found
        assert found["VALUES"].kind is SymbolKind.CONSTANT

    def test_decorators_and_imported_names_are_not_symbols(self) -> None:
        found = symbols_by_qualname(
            "from pkg.other import ImportedThing\n\n"
            "@decorator\n"
            "def build() -> None:\n"
            "    pass\n"
        )
        assert set(found) == {"build"}
        assert found["build"].decorators == ["decorator"]

    def test_output_models_carry_module_qualified_names(self) -> None:
        entry = extract(
            "class Client:\n"
            "    def send(self) -> None:\n"
            "        pass\n",
            path="httpx/_client.py",
        )
        outputs = build_extraction_outputs(entry)
        by_name = {symbol.name: symbol for symbol in outputs.symbols}
        assert by_name["send"].qualified_name == "httpx._client.Client.send"
        assert by_name["send"].parent_symbol_id == by_name["Client"].symbol_id


class TestImportRules:
    def test_internal_import_resolves_to_a_file_and_symbol(self) -> None:
        entry = extract(
            "from pkg.other import Thing\n\n"
            "def use() -> Thing:\n"
            "    return Thing()\n",
            known_paths={"pkg/other.py"},
        )
        imported = build_extraction_outputs(entry).imports[0]
        assert imported.module == "pkg.other"
        assert imported.name == "Thing"
        assert imported.resolution_status == "resolved_internal"
        assert imported.resolved_file_id == f"httpx:file:pkg/other.py@{COMMIT}"

    def test_internal_import_creates_a_file_edge(self) -> None:
        entry = extract(
            "from pkg.other import Thing\n",
            known_paths={"pkg/other.py"},
        )
        other = extract(
            "class Thing:\n    pass\n",
            path="pkg/other.py",
            known_paths={"pkg/mod.py", "pkg/other.py"},
        )
        graph = build_structural_graph(
            StructuralBatch(repo=REPO, files=[entry, other])
        )
        edges = [(edge.rel_type, edge.to_id) for edge in graph.edges]
        assert ("IMPORTS", f"httpx:file:pkg/other.py@{COMMIT}") in edges

    def test_third_party_import_becomes_an_external_package(self) -> None:
        entry = extract("import anyio\n")
        imported = build_extraction_outputs(entry).imports[0]
        assert imported.module == "anyio"
        assert imported.resolution_status == "resolved_external"
        assert imported.resolved_file_id is None

        graph = build_structural_graph(StructuralBatch(repo=REPO, files=[entry]))
        external = next(node for node in graph.nodes if node.label == "ExternalRef")
        assert external.props["kind"] == "package"
        assert external.props["name"] == "anyio"

    def test_unresolved_relative_import_is_not_external(self) -> None:
        entry = extract("from .missing import Thing\n", path="pkg/mod.py")
        assert entry.external_refs == []
        imported = build_extraction_outputs(entry).imports[0]
        assert imported.is_relative is True
        assert imported.resolution_status == "unresolved"
        assert imported.resolved_file_id is None

    def test_imports_are_split_per_bound_name(self) -> None:
        entry = extract("from pkg.other import First, Second as S\n")
        imported = build_extraction_outputs(entry).imports
        assert [(item.name, item.alias) for item in imported] == [
            ("First", None),
            ("Second", "S"),
        ]
        assert len({item.import_id for item in imported}) == 2

    def test_module_alias_is_kept(self) -> None:
        entry = extract("import os as system\n")
        imported = build_extraction_outputs(entry).imports[0]
        assert (imported.module, imported.name, imported.alias) == (
            "os",
            None,
            "system",
        )

    def test_internal_looking_unresolved_import_is_not_a_package(self) -> None:
        entry = extract("from pkg.missing import Thing\n", path="pkg/mod.py")
        imported = build_extraction_outputs(entry).imports[0]
        assert imported.resolution_status == "unresolved"


class TestConfigRules:
    def test_yaml_key_keeps_value_type_and_exact_nested_line(self) -> None:
        keys = {
            key.key: key
            for key in extract_config_keys(
                "retry:\n"
                "  max_attempts: 3\n"
                "  enabled: true\n"
                "  ratio: 0.5\n"
                "name: service\n",
                path="config.yaml",
                language=Language.YAML,
            )
        }
        assert (keys["retry.max_attempts"].value, keys["retry.max_attempts"].value_type) == (
            "3",
            "int",
        )
        assert keys["retry.max_attempts"].line == 2
        assert keys["retry.enabled"].value_type == "bool"
        assert keys["retry.ratio"].value_type == "float"
        assert keys["name"].value_type == "str"
        assert (keys["retry"].value, keys["retry"].value_type) == (None, "dict")

    def test_subscript_chain_is_a_literal_config_reference(self) -> None:
        refs = extract_config_references(
            'value = settings["retry"]["max_attempts"]\n',
            path="pkg/use.py",
        )
        assert [(ref.key, ref.line) for ref in refs] == [
            ("retry.max_attempts", 1)
        ]

    def test_dotted_attribute_chain_is_a_literal_config_reference(self) -> None:
        refs = extract_config_references(
            "value = config.retry.max_attempts\n",
            path="pkg/use.py",
        )
        assert [ref.key for ref in refs] == ["retry.max_attempts"]

    def test_called_config_attribute_is_not_a_key_read(self) -> None:
        refs = extract_config_references(
            'value = config.retry.get("max_attempts")\n',
            path="pkg/use.py",
        )
        assert refs == []

    def test_string_literal_only_matches_a_declared_key(self) -> None:
        source = 'value = resolve("retry.max_attempts")\n'
        assert extract_config_references(source, path="pkg/use.py") == []
        refs = extract_config_references(
            source,
            path="pkg/use.py",
            declared_keys={"retry.max_attempts"},
        )
        assert [ref.key for ref in refs] == ["retry.max_attempts"]

    def test_declared_key_used_by_symbol_creates_one_edge(self) -> None:
        config = extract_file(
            repo=REPO,
            path="config.yaml",
            commit_sha=COMMIT,
            source="retry:\n  max_attempts: 3\n",
            language=Language.YAML,
        )
        use = extract(
            "def read(config):\n"
            "    return config.retry.max_attempts\n",
            path="pkg/use.py",
        )
        # ``use`` comes first on purpose: declarations are indexed before
        # facts so file order cannot drop a valid USED_BY edge.
        graph = build_structural_graph(
            StructuralBatch(repo=REPO, files=[use, config])
        )
        used = [
            edge
            for edge in graph.edges
            if edge.rel_type == "USED_BY"
            and edge.from_id.endswith("config:config.yaml:retry.max_attempts")
        ]
        assert len(used) == 1
        assert used[0].to_id.endswith("pkg/use.py@" + COMMIT + ":read")

    def test_undeclared_dotted_key_creates_no_config_node_or_edge(self) -> None:
        use = extract(
            "def read(config):\n"
            "    return config.retry.max_attempts\n",
            path="pkg/use.py",
        )
        graph = build_structural_graph(
            StructuralBatch(repo=REPO, files=[use])
        )
        assert not [node for node in graph.nodes if node.label == "ConfigKey"]
        assert not [edge for edge in graph.edges if edge.rel_type == "USED_BY"]

    def test_uncertain_config_connection_creates_no_edge(self) -> None:
        config = extract_file(
            repo=REPO,
            path="config.yaml",
            commit_sha=COMMIT,
            source="retry:\n  max_attempts: 3\n",
            language=Language.YAML,
        )
        use = extract(
            'def read(config):\n'
            '    return config.retry.get("max_attempts")\n',
            path="pkg/use.py",
        )
        graph = build_structural_graph(
            StructuralBatch(repo=REPO, files=[config, use])
        )
        assert not [edge for edge in graph.edges if edge.rel_type == "USED_BY"]

    def test_output_model_carries_config_value_type(self) -> None:
        entry = extract_file(
            repo=REPO,
            path="config.yaml",
            commit_sha=COMMIT,
            source="retry:\n  max_attempts: 3\n",
            language=Language.YAML,
        )
        key = next(
            item
            for item in build_extraction_outputs(entry).config_keys
            if item.key == "retry.max_attempts"
        )
        assert (key.value, key.value_type, key.line) == ("3", "int", 2)
