"""Phase 3 contracts: one router decides what each suffix means.

Python is the only parsed language. YAML/TOML are config-key sources,
Markdown is documentation-only, and everything else is skipped unless the
path was pinned (then it is metadata only).
"""

from __future__ import annotations

from structural_helpers import COMMIT, REPO, corpus, high_signal

from app.structural.config import StructuralConfig
from app.structural.extraction.file_extractor import FileExtractor
from app.structural.extraction.models import Language
from app.structural.languages.router import (
    LANGUAGE_REGISTRY,
    ExtractionHandler,
    LanguageKind,
    route_path,
)
from app.structural.pipeline import StructuralPipeline
from app.structural.selection.file_selector import FileSelector, SelectionResult


class DictFetcher:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls: list[str] = []

    def fetch(self, *, repo: str, path: str, commit_sha: str) -> str:
        self.calls.append(path)
        return self.mapping[path]


class TestRegistry:
    def test_supported_suffixes_are_exactly_the_phase_three_table(self) -> None:
        assert LANGUAGE_REGISTRY == {
            ".py": "python",
            ".pyi": "python",
            ".yaml": "yaml_config",
            ".yml": "yaml_config",
            ".toml": "toml_config",
            ".md": "markdown",
        }

    def test_python_and_stubs_route_to_tree_sitter(self) -> None:
        for path in ("pkg/module.py", "pkg/module.pyi"):
            route = route_path(path)
            assert route.language is Language.PYTHON
            assert route.kind is LanguageKind.PYTHON
            assert route.handler is ExtractionHandler.PYTHON
            assert route.supported

    def test_yaml_and_toml_route_to_config_extraction(self) -> None:
        assert route_path("config.yml").language is Language.YAML
        assert route_path("config.yaml").handler is ExtractionHandler.CONFIG
        assert route_path("pyproject.toml").language is Language.TOML
        assert route_path("pyproject.toml").handler is ExtractionHandler.CONFIG

    def test_markdown_routes_to_doc_only(self) -> None:
        route = route_path("README.md")
        assert route.language is Language.MARKDOWN
        assert route.handler is ExtractionHandler.MARKDOWN

    def test_suffix_matching_is_case_insensitive(self) -> None:
        assert route_path("README.MD").language is Language.MARKDOWN
        assert route_path("MODULE.PYI").language is Language.PYTHON

    def test_json_javascript_notebooks_and_lock_files_are_not_routed(self) -> None:
        for path in (
            "package.json",
            "app.js",
            "lib.rs",
            "notebook.ipynb",
            "uv.lock",
            "README.rst",
        ):
            route = route_path(path)
            assert not route.supported
            assert route.language is None
            assert route.handler is ExtractionHandler.SKIP

    def test_pinned_unknown_file_is_metadata_only(self) -> None:
        route = route_path("web/app.js", pinned=True)
        assert route.supported
        assert route.pinned
        assert route.language is Language.OTHER
        assert route.handler is ExtractionHandler.METADATA


class TestSelection:
    def _select(self, records, **overrides):
        config = StructuralConfig(repo=REPO, **overrides)
        return FileSelector(config).select(
            normalized_records=records, high_signal_prs=high_signal()
        )

    def test_pyi_is_selected_as_python(self) -> None:
        result = self._select(corpus(paths=("httpx/_client.pyi",)))
        assert [(entry.path, entry.language) for entry in result.files] == [
            ("httpx/_client.pyi", Language.PYTHON)
        ]

    def test_only_routable_suffixes_are_selected(self) -> None:
        result = self._select(
            corpus(paths=("a.py", "b.yaml", "c.toml", "d.md", "e.json", "f.js"))
        )
        assert {entry.path for entry in result.files} == {
            "a.py",
            "b.yaml",
            "c.toml",
            "d.md",
        }
        reasons = {item["path"]: item["reason"] for item in result.skipped}
        assert reasons["e.json"] == "unsupported_language"
        assert reasons["f.js"] == "unsupported_language"

    def test_pinned_unknown_file_is_selected_as_other(self) -> None:
        result = self._select(
            corpus(paths=()), commit_sha=COMMIT, pinned_paths=["web/app.js"]
        )
        assert [(entry.path, entry.language, entry.reason) for entry in result.files] == [
            ("web/app.js", Language.OTHER, "manually_pinned")
        ]


class TestExtraction:
    def setup_method(self) -> None:
        self.extractor = FileExtractor()

    def extract(self, path: str, source: str, language: Language):
        return self.extractor.extract(
            repo=REPO, path=path, commit_sha=COMMIT, source=source, language=language
        )

    def test_stub_files_use_the_python_extractor(self) -> None:
        entry = self.extract(
            "pkg/module.pyi",
            "def parse(value: str) -> int: ...\n",
            Language.PYTHON,
        )
        assert [symbol.name for symbol in entry.symbols] == ["parse"]

    def test_yaml_and_toml_produce_config_keys_but_no_symbols(self) -> None:
        yaml_entry = self.extract("config.yaml", "default:\n  timeout: 5\n", Language.YAML)
        toml_entry = self.extract(
            "pyproject.toml", "[tool.ruff]\nline-length = 100\n", Language.TOML
        )
        assert {key.key for key in yaml_entry.config_keys} == {"default", "default.timeout"}
        assert toml_entry.config_keys
        assert yaml_entry.symbols == []
        assert toml_entry.symbols == []

    def test_markdown_is_metadata_only(self) -> None:
        entry = self.extract("docs/retries.md", "# Retries\n", Language.MARKDOWN)
        assert entry.symbols == []
        assert entry.config_keys == []
        assert entry.regions == []
        assert entry.content_hash

    def test_other_is_metadata_only(self) -> None:
        entry = self.extract("web/app.js", "console.log('hi')\n", Language.OTHER)
        assert entry.symbols == []
        assert entry.config_keys == []
        assert entry.regions == []
        assert entry.byte_size and entry.byte_size > 0


class TestPipelineRouting:
    def test_markdown_gets_a_doc_node_and_no_file_node(self, tmp_path) -> None:
        config = StructuralConfig(repo=REPO, commit_sha=COMMIT)
        selection = SelectionResult(
            files=[
                _selected("docs/retries.md", Language.MARKDOWN),
            ]
        )
        fetcher = DictFetcher({"docs/retries.md": "# Retries\n\nNothing named here.\n"})
        result = StructuralPipeline(config=config, fetcher=fetcher).run(selection)

        assert result.validation.ok, result.validation.errors
        labels = {node.label for node in result.graph.nodes}
        assert "Doc" in labels
        assert "File" not in labels
        assert not any(node.node_id.startswith("httpx:file:") for node in result.graph.nodes)
        doc = next(node for node in result.graph.nodes if node.label == "Doc")
        assert doc.props["path"] == "docs/retries.md"

    def test_pinned_unknown_file_gets_metadata_only(self) -> None:
        config = StructuralConfig(repo=REPO, commit_sha=COMMIT)
        selection = SelectionResult(files=[_selected("web/app.js", Language.OTHER)])
        fetcher = DictFetcher({"web/app.js": "console.log('hi')\n"})
        result = StructuralPipeline(config=config, fetcher=fetcher).run(selection)

        assert result.validation.ok, result.validation.errors
        nodes = {node.node_id: node for node in result.graph.nodes}
        node = nodes["httpx:file:web/app.js@" + COMMIT]
        assert node.props["language"] == "other"
        assert node.props["byte_size"] == len("console.log('hi')\n")
        assert result.graph.counts()["nodes"].get("Symbol", 0) == 0
        assert result.graph.counts()["nodes"].get("CodeRegion", 0) == 0


def _selected(path: str, language: Language):
    from app.structural.selection.file_selector import SelectedFile

    return SelectedFile(
        repo=REPO,
        commit_sha=COMMIT,
        path=path,
        language=language,
        reason="test",
    )
