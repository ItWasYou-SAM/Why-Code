"""Structural validation contracts, including the plan's acceptance rules."""

from structural_helpers import COMMIT, REPO, fixture_bytes, fixture_text

from app.structural.extraction.file_extractor import extract_file
from app.structural.extraction.models import (
    Language,
    StructuralBatch,
)
from app.structural.graph.graph_records import build_structural_graph
from app.structural.validation.graph_validator import (
    Neo4jStructuralValidator,
    validate_batch,
    validate_file,
    validate_graph,
)


def client_entry():
    return extract_file(
        repo=REPO,
        path="httpx/_client.py",
        commit_sha=COMMIT,
        source=fixture_bytes("sample_client.py").decode(),
        language=Language.PYTHON,
        known_paths={"httpx/_config.py", "httpx/_types.py"},
    )


class TestFileValidation:
    def test_valid_file_passes(self) -> None:
        report = validate_file(client_entry())
        assert report.ok, report.errors

    def test_symbol_beyond_file_end_fails(self) -> None:
        entry = client_entry()
        entry.symbols[0] = entry.symbols[0].model_copy(
            update={"end_line": entry.line_count + 50}
        )
        report = validate_file(entry)
        assert not report.ok
        assert any("line count" in error for error in report.errors)

    def test_symbol_start_after_end_fails(self) -> None:
        entry = client_entry()
        entry.symbols[0] = entry.symbols[0].model_copy(
            update={"start_line": 50, "end_line": 10}
        )
        assert not validate_file(entry).ok

    def test_symbol_from_another_file_fails(self) -> None:
        entry = client_entry()
        entry.symbols[0] = entry.symbols[0].model_copy(update={"path": "other.py"})
        assert not validate_file(entry).ok

    def test_region_naming_unknown_symbol_fails(self) -> None:
        entry = client_entry()
        entry.regions[0] = entry.regions[0].model_copy(
            update={"symbol_qualname": "ghost"}
        )
        assert not validate_file(entry).ok


class TestBatchValidation:
    def test_empty_batch_is_ok(self) -> None:
        assert validate_batch(StructuralBatch(repo=REPO)).ok

    def test_batch_stats_count_everything(self) -> None:
        report = validate_batch(StructuralBatch(repo=REPO, files=[client_entry()]))
        assert report.stats["files"] == 1
        assert report.stats["symbols"] > 0
        assert report.stats["regions"] == report.stats["symbols"]


class TestGraphValidation:
    def test_graph_edges_all_resolve(self) -> None:
        entry = client_entry()
        graph = build_structural_graph(
            StructuralBatch(repo=REPO, files=[entry])
        )
        report = validate_graph(graph)
        assert report.ok, report.errors

    def test_imports_may_only_target_file_or_external(self) -> None:
        entry = client_entry()
        graph = build_structural_graph(StructuralBatch(repo=REPO, files=[entry]))
        labels = {node.node_id: node.label for node in graph.nodes}
        for edge in graph.edges:
            if edge.rel_type == "IMPORTS":
                assert labels[edge.to_id] in {"File", "ExternalRef"}

    def test_broken_edge_is_caught(self) -> None:
        from app.structural.graph.graph_records import GraphEdge

        graph = build_structural_graph(
            StructuralBatch(repo=REPO, files=[client_entry()])
        )
        graph.edges.append(
            GraphEdge(rel_type="DEFINES", start_key="missing", end_key="missing2")
        )
        assert not validate_graph(graph).ok


class TestNeo4jValidation:
    def test_orphan_and_unowned_counts_are_reported(self) -> None:
        class Result:
            def __init__(self, value):
                self.value = value

            def single(self):
                return {"c": self.value}

        class Session:
            def __init__(self):
                self.values = [2, 1]

            def run(self, query, **params):
                if self.values:
                    return Result(self.values.pop(0))
                return Result(0)

        report = Neo4jStructuralValidator(Session()).report(REPO)
        assert not report.ok
        assert len(report.errors) == 2


class TestAcceptance:
    """Plan Component 3 acceptance test, expressed on the fixture file."""

    def test_known_pr_file_yields_a_symbol_with_bounds_and_region(self) -> None:
        entry = client_entry()
        assert entry.symbols, "at least one Symbol must be created"
        for symbol in entry.symbols:
            assert 1 <= symbol.start_line <= symbol.end_line
        assert entry.regions
        assert all(region.symbol_qualname for region in entry.regions)

    def test_unparseable_file_creates_no_nodes(self) -> None:
        from app.structural.config import StructuralConfig
        from app.structural.pipeline import StructuralPipeline
        from app.structural.selection.file_selector import SelectedFile, SelectionResult

        selection = SelectionResult(
            files=[
                SelectedFile(
                    repo=REPO,
                    commit_sha=COMMIT,
                    path="httpx/_broken.py",
                    language=Language.PYTHON,
                    reason="changed_in_high_signal_pr",
                )
            ]
        )

        class Fetcher:
            def fetch(self, *, repo, path, commit_sha):
                return "def broken(:\n    pass\n"

        result = StructuralPipeline(
            config=StructuralConfig(repo=REPO),
            fetcher=Fetcher(),
        ).run(selection)
        assert result.batch.files == []
        assert result.graph.nodes == []
        assert result.skipped and result.skipped[0]["path"] == "httpx/_broken.py"

    def test_config_doc_link_is_created_only_for_explicit_mentions(self) -> None:
        from app.structural.extraction.file_extractor import doc_mentions

        mentions = doc_mentions(
            fixture_text("sample_doc.md"),
            doc_path="docs/retries.md",
            known_paths={"httpx/_client.py"},
            known_config_keys={"default.retries.enabled"},
        )
        assert {
            mention.target_path for mention in mentions if mention.target_path
        } == {"httpx/_client.py"}
        assert {
            mention.config_key for mention in mentions if mention.config_key
        } == {"default.retries.enabled"}


def _graph(nodes, edges):
    from app.graph.records import GraphNode, GraphRelationship
    from app.structural.graph.graph_records import StructuralGraph

    return StructuralGraph(
        nodes=[GraphNode(label=label, key=key, properties=props) for label, key, props in nodes],
        edges=[
            GraphRelationship(rel_type=rel, start_key=a, end_key=b)
            for rel, a, b in edges
        ],
    )


class TestPhase10GraphChecks:
    def test_orphan_file_is_reported_and_warned(self) -> None:
        graph = _graph(
            [("File", "r:file:a.py@c", {"path": "a.py", "language": "python"})],
            [],
        )
        report = validate_graph(graph)
        assert report.stats["orphans"] == 1
        assert report.status == "pass_with_warnings"

    def test_config_file_with_declares_is_an_allowed_orphan(self) -> None:
        graph = _graph(
            [
                ("File", "r:file:pyproject.toml@c", {"language": "toml"}),
                ("ConfigKey", "r:config:pyproject.toml:name", {"key": "name"}),
            ],
            [("DECLARES", "r:file:pyproject.toml@c", "r:config:pyproject.toml:name")],
        )
        report = validate_graph(graph)
        assert report.stats["orphans"] == 0
        assert report.stats["allowed_orphans"] == 1

    def test_region_beyond_file_line_count_is_invalid(self) -> None:
        graph = _graph(
            [
                ("File", "r:file:a.py@c", {"language": "python", "line_count": 10}),
                (
                    "CodeRegion",
                    "r:region:a.py@c:1:50",
                    {"file_id": "r:file:a.py@c", "start_line": 1, "end_line": 50},
                ),
            ],
            [("HAS_REGION", "r:file:a.py@c", "r:region:a.py@c:1:50")],
        )
        report = validate_graph(graph)
        assert report.stats["invalid_regions"] == 1
        assert not report.ok

    def test_used_by_must_start_at_a_config_key(self) -> None:
        graph = _graph(
            [
                ("File", "r:file:a.py@c", {"language": "python"}),
                ("Symbol", "r:symbol:a.py@c:f", {"start_line": 1, "end_line": 2}),
                ("ExternalRef", "r:external:os", {}),
            ],
            [
                ("DEFINES", "r:file:a.py@c", "r:symbol:a.py@c:f"),
                ("USED_BY", "r:external:os", "r:symbol:a.py@c:f"),
            ],
        )
        report = validate_graph(graph)
        assert any("USED_BY" in error for error in report.errors)

    def test_review_anchor_coverage_reports_missing(self) -> None:
        graph = _graph(
            [
                (
                    "ReviewComment",
                    "r:review_comment:1",
                    {"path": "a.py", "line": 5},
                ),
                (
                    "ReviewComment",
                    "r:review_comment:2",
                    {"path": "a.py", "line": 99},
                ),
                (
                    "CodeRegion",
                    "r:region:a.py@c:1:10",
                    {"start_line": 1, "end_line": 10, "path": "a.py"},
                ),
            ],
            [("COMMENTS_ON", "r:review_comment:1", "r:region:a.py@c:1:10")],
        )
        report = validate_graph(graph)
        assert report.stats["missing_review_anchors"] == 1
        assert not report.ok

    def test_review_anchor_check_is_skipped_without_comments(self) -> None:
        graph = _graph(
            [("File", "r:file:a.py@c", {"language": "python"})],
            [],
        )
        report = validate_graph(graph)
        assert report.stats["missing_review_anchors"] is None
        assert "review_anchor_coverage" in report.not_checked

    def test_high_signal_region_coverage_is_enforced(self) -> None:
        graph = _graph(
            [
                ("File", "r:file:a.py@c", {"language": "python"}),
                (
                    "CodeRegion",
                    "r:region:a.py@c:1:10",
                    {"start_line": 1, "end_line": 10, "source_pr_number": 1},
                ),
            ],
            [("HAS_REGION", "r:file:a.py@c", "r:region:a.py@c:1:10")],
        )
        ok = validate_graph(graph, high_signal_pr_numbers={1})
        assert ok.ok
        missing = validate_graph(graph, high_signal_pr_numbers={1, 2})
        assert missing.stats["high_signal_prs_missing_region"] == 1
        assert not missing.ok

    def test_report_hoists_the_plan_fields(self) -> None:
        entry = client_entry()
        graph = build_structural_graph(StructuralBatch(repo=REPO, files=[entry]))
        payload = validate_graph(graph, batch=StructuralBatch(repo=REPO, files=[entry])).report()
        for key in (
            "files_parsed",
            "symbols_created",
            "imports_created",
            "config_keys_created",
            "code_regions_created",
            "relationships_created",
            "unresolved_imports",
            "unresolved_references",
            "orphans",
            "invalid_regions",
            "status",
        ):
            assert key in payload

