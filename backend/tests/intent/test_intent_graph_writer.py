"""Intent graph writer contracts.

The writer is the only I/O boundary in the intent layer. These tests pin the
non-negotiables: a dry run performs zero database operations, a dangling
relationship is refused before the first statement, an evidence-verification
failure aborts ahead of any write, and the record batch survives a JSONL
round trip.
"""

from __future__ import annotations

from typing import Any

import pytest
from intent_helpers import REPO, SYMBOL_KEY, records, structural_records

from app.core.errors import GraphWriteError, InvalidRecordError
from app.graph.records import (
    GraphNode,
    GraphRecords,
    GraphRelationship,
    records_from_jsonl,
    records_to_jsonl,
)
from app.intent.graph.intent_records import (
    INTENT_VALIDATION_VERSION,
    IntentGraph,
    build_intent_graph,
)
from app.intent.graph.neo4j_writer import IntentGraphWriter
from app.intent.models import (
    Confidence,
    DecisionIntent,
    ResolvedTarget,
    TargetType,
)
from app.intent.normalization import build_evidence_ref
from app.intent.selection.evidence_selector import EvidenceSelector
from app.intent.validation.deduplicator import ConflictEdge
from app.intent.validation.graph_link_validator import GraphIndex

EVIDENCE_ID = "httpx:review_comment:123"
OTHER_EVIDENCE_ID = "httpx:commit:" + "a" * 40
CLAIM = "Retries are limited to connection errors."


class FakeSession:
    """A session that records statements and answers the evidence probe."""

    def __init__(self, found: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._found = found

    def run(self, query: str, **parameters: Any) -> Any:
        self.calls.append((query, parameters))
        if "collect(DISTINCT id)" in query:
            ids = list(parameters.get("ids", []))
            found = ids if self._found is None else [i for i in ids if i in self._found]
            return [{"found": found}]
        return None

    def merges(self) -> list[str]:
        return [query for query, _ in self.calls if "MERGE" in query]


def make_graph() -> IntentGraph:
    fixture = records("retry_pr_evidence.json")
    bundle = EvidenceSelector(REPO).select(fixture)[0]
    index = GraphIndex.from_sources(
        repo=REPO,
        normalized_records=fixture,
        structural_records=structural_records(),
    )
    intent = DecisionIntent(
        id="dec_httpx_retry_limited_to_connection_errors",
        repo=REPO,
        pr_number=1419,
        claim=CLAIM,
        evidence_ids=[EVIDENCE_ID],
        confidence=Confidence.HIGH,
        confidence_score=0.85,
        extracted_by="gpt-4o-mini",
        extraction_version="1",
        targets=[
            ResolvedTarget(
                type=TargetType.SYMBOL,
                identifier="httpx._client.Client._send_handling_redirects",
                graph_key=SYMBOL_KEY,
            )
        ],
    )
    return build_intent_graph(
        repo=REPO,
        intents=[intent],
        evidence_refs=[build_evidence_ref(EVIDENCE_ID, bundle)],
        index=index,
    )


def test_evidence_wrapper_carries_the_packer_metadata() -> None:
    graph = make_graph()
    evidence_nodes = [node for node in graph.nodes if node.label == "Evidence"]
    assert len(evidence_nodes) == 1
    props = evidence_nodes[0].properties
    assert props["author"] == "lovelydinosaur"
    assert props["created_at"] == "2024-03-30T09:05:00Z"
    assert props["path"] == "httpx/_client.py"
    assert props["start_line"] == 100
    assert props["end_line"] == 105
    assert props["pr_number"] == 1419


def test_changed_file_and_commit_evidence_carry_their_details() -> None:
    fixture = records("retry_pr_evidence.json")
    bundle = EvidenceSelector(REPO).select(fixture)[0]
    changed = build_evidence_ref(
        "httpx:pr:1419:file:httpx/_client.py", bundle
    )
    commit = build_evidence_ref(OTHER_EVIDENCE_ID, bundle)
    assert changed.record_type == "changed_file"
    assert changed.path == "httpx/_client.py"
    assert changed.excerpt.startswith("@@")
    assert changed.pr_number == 1419
    assert commit.record_type == "commit"
    assert commit.commit_sha == "a" * 40
    assert commit.author is not None
    assert commit.occurred_at is not None


class TestConflictEdges:
    def test_conflict_edge_keeps_both_nodes_and_writes_later_to_earlier(self) -> None:
        fixture = records("retry_pr_evidence.json")
        bundle = EvidenceSelector(REPO).select(fixture)[0]
        index = GraphIndex.from_sources(
            repo=REPO,
            normalized_records=fixture,
            structural_records=structural_records(),
        )
        earlier = DecisionIntent(
            id="dec_earlier",
            repo=REPO,
            pr_number=1419,
            claim="Retries are enabled by default.",
            evidence_ids=[EVIDENCE_ID],
            confidence=Confidence.MEDIUM,
        )
        later = DecisionIntent(
            id="dec_later",
            repo=REPO,
            pr_number=1419,
            claim="Retries are disabled by default.",
            evidence_ids=[OTHER_EVIDENCE_ID],
            confidence=Confidence.MEDIUM,
        )
        graph = build_intent_graph(
            repo=REPO,
            intents=[earlier, later],
            evidence_refs=[
                build_evidence_ref(EVIDENCE_ID, bundle),
                build_evidence_ref(OTHER_EVIDENCE_ID, bundle),
            ],
            index=index,
            conflict_edges=[
                ConflictEdge(
                    start_id="dec_later",
                    end_id="dec_earlier",
                    reason="the evidence conflicts",
                )
            ],
        )
        decisions = [
            node for node in graph.nodes if node.label == "Decision"
        ]
        assert {node.key for node in decisions} == {"dec_earlier", "dec_later"}
        conflicts = [
            rel
            for rel in graph.relationships
            if rel.rel_type == "CONFLICTS_WITH"
        ]
        assert len(conflicts) == 1
        assert conflicts[0].start_key == "dec_later"
        assert conflicts[0].end_key == "dec_earlier"
        assert conflicts[0].properties["reason"] == "the evidence conflicts"


class TestPhaseEightWriteContract:
    """Phase 8: the intent writer consumes the decision-shaped write contract."""

    def test_decision_node_carries_the_phase8_write_contract(self) -> None:
        graph = make_graph()
        decisions = [node for node in graph.nodes if node.label == "Decision"]
        assert len(decisions) == 1
        node = decisions[0]
        assert node.key == "dec_httpx_retry_limited_to_connection_errors"
        assert node.properties["decision_id"] == node.key
        assert node.properties["claim"] == CLAIM
        assert node.properties["decision_type"] == "design_decision"
        assert node.properties["status"] == "active"
        assert "rationale" in node.properties
        assert node.properties["confidence_label"] == "high"
        assert node.properties["confidence_score"] == 0.85
        assert node.properties["extracted_by"] == "gpt-4o-mini"
        assert node.properties["extraction_version"] == "1"

    def test_supported_by_edge_carries_validator_version_and_support_score(self) -> None:
        graph = make_graph()
        edges = [
            rel
            for rel in graph.relationships
            if rel.rel_type == "SUPPORTED_BY"
        ]
        assert len(edges) == 1
        edge = edges[0]
        assert edge.start_key == "dec_httpx_retry_limited_to_connection_errors"
        assert edge.end_key.startswith("evi_httpx_")
        assert edge.properties["validator_version"] == INTENT_VALIDATION_VERSION
        assert edge.properties["support_score"] == 0.85

    def test_governance_edge_carries_linked_by_and_linked_at(self) -> None:
        graph = make_graph()
        edges = [
            rel
            for rel in graph.relationships
            if rel.rel_type == "GOVERNS"
        ]
        assert len(edges) == 1
        props = edges[0].properties
        assert props["linked_by"] == "intent_extractor"
        assert props["linked_at"]

    def test_write_sends_parameterized_batch_merges_only(self) -> None:
        session = FakeSession()
        graph = make_graph()
        report = IntentGraphWriter(session).write(
            graph, repo=REPO, verify_evidence=True
        )
        assert report.verified is True
        assert session.calls
        assert "collect(DISTINCT id)" in session.calls[0][0]
        merges = session.calls[1:]
        assert merges
        for query, _parameters in merges:
            assert "MERGE" in query
            assert "$nodes" in query or "$relationships" in query
        node_batch = next(query for query, _ in merges if "$nodes" in query)
        rel_batch = next(query for query, _ in merges if "$relationships" in query)
        assert "UNWIND $nodes AS node" in node_batch
        assert "UNWIND $relationships AS rel" in rel_batch
        assert "node.key" in node_batch  # matched, never interpolated


class TestDryRun:
    def test_dry_run_performs_zero_database_operations(self) -> None:
        session = FakeSession()
        graph = make_graph()
        report = IntentGraphWriter(session).write(graph, repo=REPO, dry_run=True)
        assert session.calls == []
        assert report.dry_run is True
        assert report.nodes == graph.counts()["nodes"]
        assert report.rels == graph.counts()["rels"]

    def test_rebuild_dry_run_skips_schema_and_delete(self) -> None:
        session = FakeSession()
        report = IntentGraphWriter(session).rebuild(
            make_graph(), repo=REPO, dry_run=True
        )
        assert session.calls == []
        assert report.dry_run is True


class TestIntegrity:
    def test_dangling_relationship_is_refused_without_writing(self) -> None:
        session = FakeSession()
        graph = IntentGraph(
            repo=REPO,
            nodes=[GraphNode(label="Decision", key="dec_x")],
            relationships=[
                GraphRelationship(
                    rel_type="SUPPORTED_BY",
                    start_key="dec_x",
                    end_key="missing",
                )
            ],
        )
        with pytest.raises(InvalidRecordError):
            IntentGraphWriter(session).write(graph, repo=REPO)
        assert session.calls == []


class TestWrite:
    def test_verified_write_probes_then_merges(self) -> None:
        session = FakeSession()
        graph = make_graph()
        report = IntentGraphWriter(session).write(
            graph, repo=REPO, verify_evidence=True
        )
        assert report.verified is True
        assert session.calls
        assert "collect(DISTINCT id)" in session.calls[0][0]
        assert session.merges()
        assert all(
            "MERGE" in query for query, _ in session.calls[1:]
        )

    def test_missing_evidence_aborts_before_the_first_write(self) -> None:
        session = FakeSession(found=set())
        with pytest.raises(GraphWriteError):
            IntentGraphWriter(session).write(
                make_graph(), repo=REPO, verify_evidence=True
            )
        # Only the read-only probe ran; nothing was merged.
        assert len(session.calls) == 1
        assert session.merges() == []

    def test_unverified_write_skips_the_probe(self) -> None:
        session = FakeSession()
        report = IntentGraphWriter(session).write(make_graph(), repo=REPO)
        assert report.verified is False
        assert not any("collect(DISTINCT id)" in query for query, _ in session.calls)


class TestRetrievalSetup:
    def test_schema_setup_creates_retrieval_indexes(self) -> None:
        session = FakeSession()
        IntentGraphWriter(session).prepare_schema()
        queries = "\n".join(query for query, _ in session.calls)
        assert "CREATE FULLTEXT INDEX decision_fulltext" in queries
        assert "CREATE VECTOR INDEX decision_embedding" in queries
        assert "CREATE VECTOR INDEX evidence_embedding" in queries

    def test_write_embeds_decision_and_evidence_nodes(self) -> None:
        session = FakeSession()
        IntentGraphWriter(session).write(make_graph(), repo=REPO)
        decision_nodes = [
            entry
            for query, parameters in session.calls
            if "MERGE (n:Decision" in query
            for entry in parameters["nodes"]
        ]
        evidence_nodes = [
            entry
            for query, parameters in session.calls
            if "MERGE (n:Evidence" in query
            for entry in parameters["nodes"]
        ]
        assert decision_nodes and evidence_nodes
        assert all(
            len(entry["properties"]["embedding"]) == 1536
            for entry in decision_nodes
        )
        assert all(
            len(entry["properties"]["embedding"]) == 1536
            for entry in evidence_nodes
        )


class TestJsonl:
    def test_graph_records_survive_a_jsonl_round_trip(self) -> None:
        original: GraphRecords = make_graph().to_records()
        restored = records_from_jsonl(records_to_jsonl(original).splitlines())
        assert restored == original
