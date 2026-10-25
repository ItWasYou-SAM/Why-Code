"""Graph writer contracts: pure plan first, writes second."""

import pytest
from structural_helpers import COMMIT, REPO, FakeSession, corpus

from app.core.errors import GraphWriteError, InvalidRecordError
from app.structural.config import StructuralConfig
from app.structural.content.file_content_fetcher import RecordContentFetcher
from app.structural.extraction.models import (
    Language,
    StructuralBatch,
    StructuralFile,
)
from app.structural.graph.neo4j_writer import StructuralGraphWriter, translate
from app.structural.pipeline import StructuralPipeline
from app.structural.selection.file_selector import FileSelector


def graph():
    records = corpus(paths=("httpx/_client.py", "httpx/_config.py", "config/defaults.yaml"))
    selection = FileSelector(StructuralConfig(repo=REPO, commit_sha=COMMIT)).select(
        normalized_records=records, high_signal_prs=[{"pr_number": 1131}]
    )
    pipeline = StructuralPipeline(
        config=StructuralConfig(repo=REPO, commit_sha=COMMIT),
        fetcher=RecordContentFetcher(records),
    )
    # Content comes from the fixture files directly, not the corpus.
    from structural_helpers import fixture_text

    class FixtureFetcher:
        def fetch(self, *, repo, path, commit_sha):
            if path == "httpx/_client.py":
                return fixture_text("sample_client.py")
            if path == "httpx/_config.py":
                return fixture_text("sample_config_module.py")
            if path == "config/defaults.yaml":
                return fixture_text("sample_config.yaml")
            raise InvalidRecordError(f"no fixture for {path}")

    pipeline.fetcher = FixtureFetcher()
    result = pipeline.run(selection)
    return result.graph


class TestGraphRecords:
    def test_file_nodes_carry_path_and_commit(self) -> None:
        nodes = {node.node_id: node for node in graph().nodes}
        fid = f"httpx:file:httpx/_client.py@{COMMIT}"
        assert nodes[fid].label == "File"
        assert nodes[fid].props["commit_sha"] == COMMIT
        assert nodes[fid].props["repo"] == REPO

    def test_file_defines_its_symbols(self) -> None:
        edges = {(edge.rel_type, edge.from_id, edge.to_id) for edge in graph().edges}
        fid = f"httpx:file:httpx/_client.py@{COMMIT}"
        sid = f"httpx:symbol:httpx/_client.py@{COMMIT}:Client"
        assert ("DEFINES", fid, sid) in edges

    def test_symbol_owns_its_region(self) -> None:
        edges = [edge for edge in graph().edges if edge.rel_type == "HAS_REGION"]
        assert any(edge.from_id.startswith("httpx:symbol:") for edge in edges)
        assert any(edge.from_id.startswith("httpx:file:") for edge in edges)

    def test_definition_regions_carry_source_file_and_hash(self) -> None:
        nodes = {node.node_id: node for node in graph().nodes}
        region = next(
            node for node in nodes.values() if node.label == "CodeRegion"
        )
        assert region.props["source"] == "definition"
        assert region.props["file_id"].startswith("httpx:file:")
        assert region.props["content_hash"]

    def test_regions_and_symbols_carry_embedding_source_text(self) -> None:
        nodes = graph().nodes
        region = next(node for node in nodes if node.label == "CodeRegion")
        symbol = next(
            node for node in nodes if node.label == "Symbol" and node.props["name"] == "Client"
        )
        assert region.props["excerpt"]
        assert symbol.props["docstring"] == "A minimal client."

    def test_file_imports_resolvable_file(self) -> None:
        edges = [
            edge
            for edge in graph().edges
            if edge.rel_type == "IMPORTS" and edge.to_id.startswith("httpx:file:")
        ]
        assert edges, "expected a file-to-file import edge"

    def test_unresolvable_import_becomes_external_ref(self) -> None:
        nodes = {node.node_id: node for node in graph().nodes}
        assert "httpx:external:os" in nodes
        assert nodes["httpx:external:os"].label == "ExternalRef"

    def test_config_key_is_declared_by_file(self) -> None:
        edges = {(edge.rel_type, edge.to_id) for edge in graph().edges}
        assert any(
            rel == "DECLARES" and target.startswith("httpx:config:")
            for rel, target in edges
        )

    def test_config_key_used_by_symbol(self) -> None:
        edges = [edge for edge in graph().edges if edge.rel_type == "USED_BY"]
        assert any(edge.to_id.startswith("httpx:symbol:") for edge in edges)

    def test_every_edge_endpoint_exists(self) -> None:
        graph().assert_integrity()

    def test_no_duplicate_node_ids(self) -> None:
        ids = [node.node_id for node in graph().nodes]
        assert len(ids) == len(set(ids))

    def test_counts_are_stable_across_builds(self) -> None:
        assert graph().counts() == graph().counts()

    def test_graph_is_made_of_shared_records(self) -> None:
        from app.graph.records import GraphNode, GraphRecords, GraphRelationship

        structural = graph()
        assert all(isinstance(node, GraphNode) for node in structural.nodes)
        assert all(isinstance(edge, GraphRelationship) for edge in structural.edges)
        records = structural.to_records()
        assert isinstance(records, GraphRecords)
        assert records.counts() == {
            "nodes": structural.counts()["nodes"],
            "rels": structural.counts()["edges"],
        }


class TestWriter:
    def test_schema_setup_creates_retrieval_indexes(self) -> None:
        session = FakeSession()
        StructuralGraphWriter(session).prepare_schema()
        queries = "\n".join(session.queries)
        assert "CREATE FULLTEXT INDEX decision_fulltext" in queries
        assert "CREATE VECTOR INDEX symbol_embedding" in queries
        assert "CREATE VECTOR INDEX code_region_embedding" in queries

    def test_write_attaches_embeddings_to_indexed_nodes(self) -> None:
        session = FakeSession()
        StructuralGraphWriter(session).write(graph(), repo=REPO)
        embedded = [
            entry
            for query, parameters in session.calls
            if "MERGE (n:Symbol" in query
            for entry in parameters["nodes"]
        ]
        assert embedded
        assert all(len(entry["properties"]["embedding"]) == 1536 for entry in embedded)

    def test_dry_run_performs_zero_writes(self) -> None:
        session = FakeSession()
        writer = StructuralGraphWriter(session)
        report = writer.write(graph(), repo=REPO, dry_run=True)
        assert session.executed == 0
        assert report.nodes["File"] == 3

    def test_write_is_parameterized(self) -> None:
        session = FakeSession()
        writer = StructuralGraphWriter(session)
        writer.write(graph(), repo=REPO)
        assert session.executed > 0
        for query, _params in session.calls:
            assert "$nodes" in query or "$relationships" in query
            assert "httpx:" not in query

    def test_write_batches_by_label_and_relationship(self) -> None:
        structural = graph()
        statements = translate(structural)
        nodes_by_label = {node.label for node in structural.nodes}
        rels_by_type = {edge.rel_type for edge in structural.edges}
        assert len(statements) == len(nodes_by_label) + len(rels_by_type)
        assert len(statements) < len(structural.nodes) + len(structural.edges)

    def test_write_batch_size_splits_statements(self) -> None:
        structural = graph()
        statements = translate(structural, batch_size=1)
        assert len(statements) == len(structural.nodes) + len(structural.edges)

    def test_validation_failure_blocks_writes(self) -> None:
        bad = StructuralFile(
            repo=REPO,
            path="a.py",
            commit_sha=COMMIT,
            language=Language.PYTHON,
            line_count=1,
        )
        from app.structural.extraction.models import Symbol, SymbolKind

        bad.symbols.append(
            Symbol(
                symbol_id="httpx:symbol:a.py@x:bad",
                name="bad",
                qualname="bad",
                kind=SymbolKind.FUNCTION,
                path="a.py",
                commit_sha=COMMIT,
                start_line=10,
                end_line=20,
            )
        )
        session = FakeSession()
        pipeline = StructuralPipeline(
            config=StructuralConfig(repo=REPO),
            fetcher=RecordContentFetcher([]),
            writer=StructuralGraphWriter(session),
        )
        result = pipeline.run(
            __import__(
                "app.structural.selection.file_selector", fromlist=["SelectionResult"]
            ).SelectionResult()
        )
        # A direct batch with a bad symbol never reaches the writer.
        from app.structural.validation.graph_validator import validate_batch

        report = validate_batch(StructuralBatch(repo=REPO, files=[bad]))
        assert not report.ok
        assert session.executed == 0
        _ = result

    def test_writer_wraps_unexpected_failures(self) -> None:
        class Boom(FakeSession):
            def run(self, query, **parameters):
                raise RuntimeError("connection lost")

        with pytest.raises(GraphWriteError):
            StructuralGraphWriter(Boom()).write(graph(), repo=REPO)

    def test_rebuild_deletes_only_structural_labels(self) -> None:
        session = FakeSession()
        StructuralGraphWriter(session).rebuild(graph(), repo=REPO)
        deletes = [query for query in session.queries if "DETACH DELETE" in query]
        assert deletes
        assert all("repo" in query for query in deletes)
        assert any(":File" in query for query in deletes)
