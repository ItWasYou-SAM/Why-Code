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
from app.intent.graph.intent_records import IntentGraph, build_intent_graph
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


class TestJsonl:
    def test_graph_records_survive_a_jsonl_round_trip(self) -> None:
        original: GraphRecords = make_graph().to_records()
        restored = records_from_jsonl(records_to_jsonl(original).splitlines())
        assert restored == original
