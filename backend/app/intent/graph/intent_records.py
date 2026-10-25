"""Intent graph records.

The writer layer consumes a self-contained batch: intent nodes, one
``Evidence`` wrapper per cited source record, minimal stubs for pre-existing
endpoints, and only validated relationships. Stubs make the batch
integrity-checkable without pretending to re-own data another component
already wrote (D-0036).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.core.errors import InvalidRecordError
from app.graph.records import GraphNode, GraphRecords, GraphRelationship
from app.intent.models import (
    ConstraintIntent,
    DecisionIntent,
    EvidenceRef,
    IncidentIntent,
    IntentBase,
    Participant,
    ReversalIntent,
    TargetType,
)
from app.intent.validation.deduplicator import ConflictEdge
from app.intent.validation.graph_link_validator import GraphIndex
from app.intent.validation.supersession import SupersessionEdge

TARGET_LABEL = {
    TargetType.SYMBOL: "Symbol",
    TargetType.CONFIG_KEY: "ConfigKey",
    TargetType.FILE: "File",
    TargetType.CODE_REGION: "CodeRegion",
}

ROLE_REL = {
    "AUTHORED": "AUTHORED",
    "REVIEWED": "REVIEWED",
    "ADVOCATED": "ADVOCATED",
    "OBJECTED_TO": "OBJECTED_TO",
}

# Phase 8 write contract: provenance stamps for the intent graph. The
# validator version names the evidence-validation firewall that produced the
# acceptance verdicts backing each ``SUPPORTED_BY`` edge; the linker names the
# component that linked an accepted intent to its governance target.
INTENT_VALIDATION_VERSION = "phase4"
INTENT_LINKER = "intent_extractor"


def _linked_at() -> str:
    """The governance link timestamp, ISO-8601 UTC (JSON-safe for the batch)."""
    return datetime.now(UTC).isoformat()


def intent_label(intent: IntentBase | IncidentIntent) -> str:
    if isinstance(intent, DecisionIntent):
        return "Decision"
    if isinstance(intent, ConstraintIntent):
        return "Constraint"
    if isinstance(intent, ReversalIntent):
        return "Reversal"
    return "Incident"


@dataclass
class IntentGraph:
    repo: str
    nodes: list[GraphNode] = field(default_factory=list)
    relationships: list[GraphRelationship] = field(default_factory=list)
    intents: list[IntentBase] = field(default_factory=list)
    incidents: list[IncidentIntent] = field(default_factory=list)
    participants: list[Participant] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    def node_keys(self) -> set[str]:
        return {node.key for node in self.nodes}

    def counts(self) -> dict[str, dict[str, int]]:
        nodes: dict[str, int] = {}
        rels: dict[str, int] = {}
        for node in self.nodes:
            nodes[node.label] = nodes.get(node.label, 0) + 1
        for rel in self.relationships:
            rels[rel.rel_type] = rels.get(rel.rel_type, 0) + 1
        return {"nodes": nodes, "rels": rels}

    def to_records(self) -> GraphRecords:
        records = GraphRecords(nodes=list(self.nodes), relationships=list(self.relationships))
        records.assert_integrity()
        return records

    def assert_integrity(self) -> None:
        self.to_records()


def build_intent_graph(
    *,
    repo: str,
    intents: list[IntentBase],
    evidence_refs: list[EvidenceRef],
    incidents: list[IncidentIntent] | None = None,
    participants: list[Participant] | None = None,
    index: GraphIndex,
    supersession_edges: list[SupersessionEdge] | None = None,
    conflict_edges: list[ConflictEdge] | None = None,
) -> IntentGraph:
    incidents = sorted(incidents or [], key=lambda item: item.id)
    participants = sorted(
        participants or [],
        key=lambda item: (item.person_id, item.role, item.target_id),
    )
    refs_by_id = {ref.evidence_id: ref for ref in evidence_refs}
    ordered_intents = sorted(intents, key=lambda item: (item.pr_number, item.id))
    labels_by_id = {
        intent.id: intent_label(intent) for intent in ordered_intents
    }
    linked_at = _linked_at()

    graph = IntentGraph(repo=repo, intents=ordered_intents, incidents=incidents,
                        participants=participants, evidence_refs=list(evidence_refs))
    nodes: dict[str, GraphNode] = {}
    relationships: list[GraphRelationship] = []

    def ensure_node(
        label: str, key: str, properties: dict[str, Any] | None = None
    ) -> None:
        payload = {"record_id": key, "repo": repo, **(properties or {})}
        existing = nodes.get(key)
        if existing is None:
            nodes[key] = GraphNode(label=label, key=key, properties=payload)
        elif properties:
            merged = {**existing.properties, **payload}
            nodes[key] = GraphNode(label=existing.label, key=key, properties=merged)

    def add_rel(
        rel_type: str,
        start_key: str,
        end_key: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        candidate = GraphRelationship(
            rel_type=rel_type,
            start_key=start_key,
            end_key=end_key,
            properties=properties or {},
        )
        if candidate not in relationships:
            relationships.append(candidate)

    # Evidence wrappers first: every citation resolves to one.
    for ref in sorted(refs_by_id.values(), key=lambda item: item.evidence_id):
        ensure_node(
            "Evidence",
            ref.node_id,
            {
                "evidence_id": ref.evidence_id,
                "source_record_id": ref.source_record_id,
                "record_type": ref.record_type,
                "pr_number": ref.pr_number,
                "issue_number": ref.issue_number,
                "commit_sha": ref.commit_sha,
                "author": ref.author,
                "created_at": ref.occurred_at,
                "path": ref.path,
                "start_line": ref.start_line,
                "end_line": ref.end_line,
                "url": ref.url,
                "excerpt": ref.excerpt,
                "content_hash": ref.content_hash,
            },
        )
        if index.has_node(ref.source_record_id):
            label = index.label_of(ref.source_record_id)
            if label:
                ensure_node(label, ref.source_record_id)
                add_rel("REPRESENTS", ref.node_id, ref.source_record_id)
        else:
            graph.skipped.append(
                f"evidence {ref.evidence_id} has no source node in the graph"
            )

    incident_ids_by_evidence: dict[str, list[str]] = {}
    for incident in incidents:
        ensure_node(
            "Incident",
            incident.id,
            {
                "kind": incident.kind.value,
                "issue_id": incident.issue_id,
                "summary": incident.summary,
                "status": incident.status.value,
                "pr_numbers": incident.pr_numbers,
                "evidence_ids": incident.evidence_ids,
            },
        )
        for evidence_id in incident.evidence_ids:
            incident_ids_by_evidence.setdefault(evidence_id, []).append(incident.id)

    for intent in ordered_intents:
        label = intent_label(intent)
        ensure_node(label, intent.id, _intent_properties(intent))
        for evidence_id in intent.evidence_ids:
            evidence_ref = refs_by_id.get(evidence_id)
            if evidence_ref is None:
                raise InvalidRecordError(
                    f"intent {intent.id} cites evidence with no wrapper: {evidence_id}",
                    component="intent",
                    record_id=intent.id,
                )
            add_rel(
                "SUPPORTED_BY",
                intent.id,
                evidence_ref.node_id,
                {
                    # Phase 8 write contract: the evidence link carries the
                    # validation version that accepted the citation and the
                    # support score the acceptance produced.
                    "validator_version": INTENT_VALIDATION_VERSION,
                    "support_score": intent.confidence_score,
                },
            )
        for target in intent.targets:
            target_label = TARGET_LABEL[target.type]
            ensure_node(target_label, target.graph_key)
            add_rel(
                "GOVERNS",
                intent.id,
                target.graph_key,
                {
                    "target_type": target.type.value,
                    "resolution": target.resolution,
                    # Phase 8 write contract: the governance link records who
                    # performed the linking and when.
                    "linked_by": INTENT_LINKER,
                    "linked_at": linked_at,
                },
            )
        for produced_key in intent.produced_in:
            produced_label = index.label_of(produced_key) or _guess_label(produced_key)
            ensure_node(produced_label, produced_key)
            add_rel("PRODUCED_IN", intent.id, produced_key)
        for incident_id in sorted(
            {
                candidate
                for evidence_id in intent.evidence_ids
                for candidate in incident_ids_by_evidence.get(evidence_id, [])
            }
        ):
            add_rel("MOTIVATED_BY", intent.id, incident_id)
        if intent.superseded_by:
            # The pointer is a property on the earlier node. The relationship
            # itself is emitted from ``supersession_edges`` so only the three
            # Phase 6 types can ever reach the graph.
            ensure_node("Decision", intent.superseded_by)

    # ``ReversalIntent.reverses`` is retained as an auditable property, but the
    # relationship itself comes only from ``supersession_edges``. That keeps
    # every REVERSES edge tied to an adjudicated verdict and avoids writing a
    # second, property-free duplicate when the detector also records the edge.
    for edge in supersession_edges or []:
        ensure_node("Decision", edge.end_id)
        ensure_node(
            labels_by_id.get(edge.start_id, "Decision"),
            edge.start_id,
        )
        add_rel(
            edge.rel_type,
            edge.start_id,
            edge.end_id,
            {"reason": edge.reason},
        )

    # Phase 7 conflicts keep both nodes. The edge is the authoritative
    # conflict marker; the status and contested_by property are audit aids.
    for conflict in conflict_edges or []:
        ensure_node(
            labels_by_id.get(conflict.end_id, "Decision"),
            conflict.end_id,
        )
        ensure_node(
            labels_by_id.get(conflict.start_id, "Decision"),
            conflict.start_id,
        )
        add_rel(
            conflict.rel_type,
            conflict.start_id,
            conflict.end_id,
            {"reason": conflict.reason},
        )

    for participant in participants:
        ensure_node(
            "Person",
            participant.person_id,
            {"login": participant.login},
        )
        target_label = _label_for_target_type(participant.target_type, index)
        ensure_node(target_label, participant.target_id)
        add_rel(
            ROLE_REL[participant.role],
            participant.person_id,
            participant.target_id,
            {
                "basis": participant.basis,
                "evidence_id": participant.evidence_id,
            },
        )

    graph.nodes = [nodes[key] for key in sorted(nodes)]
    graph.relationships = relationships
    graph.assert_integrity()
    return graph


def _intent_properties(intent: IntentBase) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "kind": intent.kind.value,
        "claim": intent.claim,
        "rationale": intent.rationale,
        "status": intent.status.value,
        "confidence": intent.confidence.value,
        "confidence_reasons": intent.confidence_reasons,
        "pr_number": intent.pr_number,
        "source_candidate": intent.source_candidate,
        "duplicate_of": intent.duplicate_of,
        "superseded_by": intent.superseded_by,
        "contested_by": intent.contested_by,
        "missing_evidence": intent.missing_evidence,
        "target_keys": [target.graph_key for target in intent.targets],
        "evidence_ids": intent.evidence_ids,
        "produced_in": intent.produced_in,
        "merged_from": intent.merged_from,
        "decision_type": intent.decision_type.value,
        "extraction_status": intent.extraction_status.value,
        # Neo4j properties are scalar or a list of scalars, so the temporal
        # scope is flattened and participant classifications are strings.
        "valid_from": (
            intent.temporal_scope.valid_from if intent.temporal_scope else None
        ),
        "valid_until": (
            intent.temporal_scope.valid_until if intent.temporal_scope else None
        ),
        "participants_proposed": [
            f"{participant.login}:{participant.role.value}"
            for participant in intent.participants_proposed
        ],
        # Phase 8 write contract: the decision-shaped node payload. The
        # categorical ``confidence`` label carries the display vocabulary and
        # ``confidence_score`` the reproducible audit trail behind it; the
        # extraction provenance names the proposal and its version.
        "confidence_label": intent.confidence.value,
        "confidence_score": intent.confidence_score,
        "extracted_by": intent.extracted_by,
        "extraction_version": intent.extraction_version,
    }
    if isinstance(intent, ConstraintIntent):
        properties["constraint_type"] = intent.constraint_type
    if isinstance(intent, ReversalIntent):
        properties["reversal_type"] = intent.reversal_type
        properties["reversed_claim"] = intent.reversed_claim
        properties["reverses"] = intent.reverses
    if isinstance(intent, DecisionIntent):
        properties["proposed_id"] = intent.proposed_id
        properties["decision_id"] = intent.id
    return properties


def _guess_label(key: str) -> str:
    if ":commit:" in key:
        return "Commit"
    if ":pr:" in key:
        return "PullRequest"
    if ":person:" in key:
        return "Person"
    if ":doc:" in key:
        return "Doc"
    return "Decision"


def _label_for_target_type(target_type: str, index: GraphIndex) -> str:
    if target_type in {"PullRequest", "Commit"}:
        return target_type
    if target_type in {"decision", "constraint", "reversal", "incident"}:
        return target_type.capitalize()
    try:
        return TARGET_LABEL[TargetType(target_type)]
    except ValueError:
        label = index.label_of(target_type)
        if label:
            return label
        raise InvalidRecordError(
            f"cannot resolve label for participant target type {target_type!r}",
            component="intent",
        ) from None


__all__ = [
    "IntentGraph",
    "ROLE_REL",
    "TARGET_LABEL",
    "build_intent_graph",
    "intent_label",
]
