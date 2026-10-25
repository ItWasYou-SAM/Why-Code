"""Canonical intent models.

These are the *validated* forms, not the LLM-facing schemas. A model never
chooses ``confidence``, ``status`` or graph links: those are computed from
evidence by deterministic code and carried here for the writer, review
queue and retrieval layer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IntentKind(StrEnum):
    DECISION = "decision"
    CONSTRAINT = "constraint"
    REVERSAL = "reversal"
    INCIDENT = "incident"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INSUFFICIENT = "insufficient"


class DecisionStatus(StrEnum):
    ACTIVE = "active"
    CONTESTED = "contested"
    SUPERSEDED = "superseded"
    REVERSED = "reversed"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


class DecisionType(StrEnum):
    DESIGN_DECISION = "design_decision"
    CONSTRAINT = "constraint"
    REVERSAL = "reversal"
    INCIDENT_RESPONSE = "incident_response"
    API_CONTRACT = "api_contract"
    OPERATIONAL_POLICY = "operational_policy"


class ExtractionStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    REVERSED = "reversed"
    REVISED = "revised"
    UNCERTAIN = "uncertain"


class ParticipantRole(StrEnum):
    AUTHOR = "author"
    REVIEWER = "reviewer"
    ADVOCATE = "advocate"
    OBJECTOR = "objector"
    MAINTAINER = "maintainer"
    COMMENTER = "commenter"


class IncidentStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"


class TargetType(StrEnum):
    SYMBOL = "Symbol"
    CONFIG_KEY = "ConfigKey"
    FILE = "File"
    CODE_REGION = "CodeRegion"


class EvidenceRef(BaseModel):
    """A validated pointer to an existing ingestion record.

    ``evidence_id`` is the source record ID. ``node_id`` is the graph key of
    the wrapper ``Evidence`` node created by this component, so a source node
    and its evidence wrapper never collide.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    record_type: str = Field(min_length=1)
    source_record_id: str = Field(min_length=1)
    pr_number: int | None = None
    issue_number: int | None = None
    commit_sha: str | None = None
    author: str | None = None
    occurred_at: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    excerpt: str = ""
    url: str | None = None
    content_hash: str | None = None


class ResolvedTarget(BaseModel):
    """A target whose graph node was proven to exist before linking."""

    model_config = ConfigDict(extra="forbid")

    type: TargetType
    identifier: str = Field(min_length=1)
    graph_key: str = Field(min_length=1)
    resolution: Literal["exact", "alias"] = "exact"
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class TargetOption(BaseModel):
    """One target the model is allowed to mention, copied from the graph."""

    model_config = ConfigDict(extra="forbid")

    type: TargetType
    identifier: str = Field(min_length=1)
    graph_key: str = Field(min_length=1)
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    name: str | None = None
    commit_sha: str | None = None

    def prompt_line(self) -> str:
        parts = [f"- {self.type.value}: {self.identifier}"]
        parts.append(f"key={self.graph_key}")
        if self.path:
            parts.append(f"path={self.path}")
        if self.start_line is not None and self.end_line is not None:
            parts.append(f"lines={self.start_line}-{self.end_line}")
        return " | ".join(parts)

    def to_resolved(self, resolution: str) -> ResolvedTarget:
        return ResolvedTarget(
            type=self.type,
            identifier=self.identifier,
            graph_key=self.graph_key,
            resolution=resolution,  # type: ignore[arg-type]
            path=self.path,
            start_line=self.start_line,
            end_line=self.end_line,
        )


class ConfidenceResult(BaseModel):
    """A categorical level plus the numeric audit trail behind it.

    ``score`` exists for replay and review; it is never rendered as a
    percentage in the UI.
    """

    level: Confidence
    score: float = Field(default=0.0, ge=0.0, le=0.98)
    reasons: list[str] = Field(default_factory=list)
    source_types: list[str] = Field(default_factory=list)


class TemporalScope(BaseModel):
    """The period a claim is asserted to hold over its targets."""

    model_config = ConfigDict(extra="forbid")

    valid_from: str = Field(min_length=1)
    valid_until: str | None = None


class ProposedParticipantRef(BaseModel):
    """A model-proposed person/role link, preserved beside deterministic roles.

    Participant *edges* are still produced by ``ParticipantExtractor`` from
    record fields and explicit stance language. This model keeps the
    extraction classification auditable without letting it create a graph
    relationship on its own.
    """

    model_config = ConfigDict(extra="forbid")

    login: str = Field(min_length=1)
    role: ParticipantRole
    evidence_ids: list[str] = Field(default_factory=list)


class IntentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: IntentKind
    repo: str = Field(min_length=1)
    pr_number: int
    claim: str = Field(min_length=1)
    rationale: str = ""
    targets: list[ResolvedTarget] = Field(default_factory=list)
    targets_proposed: list[str] = Field(default_factory=list)
    targets_dropped: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    produced_in: list[str] = Field(default_factory=list)
    confidence: Confidence
    confidence_reasons: list[str] = Field(default_factory=list)
    decision_type: DecisionType = DecisionType.DESIGN_DECISION
    extraction_status: ExtractionStatus = ExtractionStatus.UNCERTAIN
    temporal_scope: TemporalScope | None = None
    participants_proposed: list[ProposedParticipantRef] = Field(
        default_factory=list
    )
    status: DecisionStatus = DecisionStatus.ACTIVE
    superseded_by: str | None = None
    contested_by: str | None = None
    duplicate_of: str | None = None
    # Phase 7: the IDs folded into this canonical node by duplicate
    # adjudication. The duplicate nodes are not written, but the merge trail
    # remains auditable on the survivor.
    merged_from: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    source_candidate: str = "DECISION_CANDIDATE"
    # Phase 8 write contract: the numeric audit trail behind the categorical
    # ``confidence`` label, plus who produced and which extraction version
    # accepted the proposal. They are writer inputs, never model inputs.
    confidence_score: float = Field(default=0.0, ge=0.0, le=0.98)
    extracted_by: str = Field(default="", max_length=200)
    extraction_version: str = Field(default="", max_length=200)

    @field_validator("merged_from")
    @classmethod
    def _unique_merged_from(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for merged_id in value:
            if not merged_id.strip():
                raise ValueError("merged_from ids must be non-empty")
            if merged_id in seen:
                raise ValueError(f"duplicate merged_from id {merged_id!r}")
            seen.add(merged_id)
        return value

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for evidence_id in value:
            if not evidence_id.strip():
                raise ValueError("evidence ids must be non-empty")
            if evidence_id in seen:
                raise ValueError(f"duplicate evidence id {evidence_id!r}")
            seen.add(evidence_id)
        return value


class DecisionIntent(IntentBase):
    kind: Literal[IntentKind.DECISION] = IntentKind.DECISION
    proposed_id: str | None = None


class ConstraintIntent(IntentBase):
    kind: Literal[IntentKind.CONSTRAINT] = IntentKind.CONSTRAINT
    constraint_type: str = "other"


class ReversalIntent(IntentBase):
    kind: Literal[IntentKind.REVERSAL] = IntentKind.REVERSAL
    reversal_type: str = "other"
    reversed_claim: str = ""
    reverses: list[str] = Field(default_factory=list)


class IncidentIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal[IntentKind.INCIDENT] = IntentKind.INCIDENT
    repo: str = Field(min_length=1)
    issue_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    pr_numbers: list[int] = Field(default_factory=list)
    evidence_ids: list[str] = Field(min_length=1)
    status: IncidentStatus = IncidentStatus.ACTIVE


class Participant(BaseModel):
    """One person-to-intent/change role.

    A role is emitted only when the evidence names the person and the role
    (authorship, review submission, or explicit stance language). The
    ``basis`` field keeps the inference auditable.
    """

    model_config = ConfigDict(extra="forbid")

    person_id: str = Field(min_length=1)
    login: str = Field(min_length=1)
    role: Literal["AUTHORED", "REVIEWED", "ADVOCATED", "OBJECTED_TO"]
    target_id: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    evidence_id: str | None = None
    basis: str = ""


class ReviewReason(StrEnum):
    LOW_SIGNAL = "low_signal"
    LLM_ERROR = "llm_error"
    INVALID_SCHEMA = "invalid_schema"
    INVALID_EVIDENCE = "invalid_evidence"
    UNRESOLVED_TARGET = "unresolved_target"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    INVALID_TEMPORAL = "invalid_temporal"
    RULE_VIOLATION = "rule_violation"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    POSSIBLE_DUPLICATE = "possible_duplicate"
    CONTESTED = "contested"


class ReviewItem(BaseModel):
    """One human decision over an uncertain extraction.

    Phase 8/9 write contract: the queue item carries the candidate identity,
    the claim under review, the queue status, the evidence that was cited, the
    resolved targets and the proposed confidence — plus the reviewer's own
    decision. Nothing here is ever a silent auto-merge.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    reason: ReviewReason
    repo: str = Field(min_length=1)
    pr_number: int | None = None
    candidate_class: str | None = None
    claim: str = ""
    status: str = "needs_review"
    evidence: list[str] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    proposed_confidence: Confidence = Confidence.MEDIUM
    reviewer_decision: str | None = None
    issues: list[str] = Field(default_factory=list)
    raw_output: str | None = None
    created_at: str = ""


class IntentSummary(BaseModel):
    """Deterministic run counters; used by the CLI report and tests."""

    prs_scanned: int = 0
    candidates: dict[str, int] = Field(default_factory=dict)
    proposals: int = 0
    accepted: dict[str, int] = Field(default_factory=dict)
    rejected: int = 0
    review_items: int = 0
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = [
    "Confidence",
    "ConfidenceResult",
    "ConstraintIntent",
    "DecisionIntent",
    "DecisionStatus",
    "DecisionType",
    "EvidenceRef",
    "ExtractionStatus",
    "IncidentIntent",
    "IncidentStatus",
    "IntentBase",
    "IntentKind",
    "IntentSummary",
    "Participant",
    "ParticipantRole",
    "ProposedParticipantRef",
    "ResolvedTarget",
    "ReviewItem",
    "ReviewReason",
    "ReversalIntent",
    "TargetOption",
    "TargetType",
    "TemporalScope",
]
