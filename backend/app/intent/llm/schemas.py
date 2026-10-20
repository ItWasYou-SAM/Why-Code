"""Strict, provider-facing schemas for extraction (Phase 3), supersession
adjudication (Phase 6) and duplicate adjudication (Phase 7).

For extraction, the model returns one candidate envelope per call. The shape
is deliberately narrow: no confidence, no graph key, no supersession edge and
no database identifier. ``status`` and ``decision_type`` are classification
labels, not lifecycle state: deterministic code still computes the canonical
``active``/``contested``/``superseded``/``reversed`` state and every graph
edge.

For supersession, ``SupersessionVerdict`` asks the model only for the
relationship over an already-found candidate pair. It cannot discover pairs
or write edges.

``extra="forbid"`` is set on every model, so a provider that ignores the
supplied JSON schema cannot smuggle an unknown field through validation.
``model_json_schema()`` is the schema sent to the provider; the tests pin
required keys, enums, length limits and ``additionalProperties: false`` at
every level.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

TargetTypeLiteral = Literal["file", "symbol", "config_key", "code_region"]
DecisionTypeLiteral = Literal[
    "design_decision",
    "constraint",
    "reversal",
    "incident_response",
    "api_contract",
    "operational_policy",
]
ExtractionStatusLiteral = Literal[
    "accepted",
    "rejected",
    "reversed",
    "revised",
    "uncertain",
]
SupersessionRelationshipLiteral = Literal[
    "reverses",
    "revises",
    "supersedes",
    "unrelated",
    "insufficient_evidence",
]
DuplicateRelationshipLiteral = Literal[
    "merge",
    "keep_separate",
    "conflicts",
    "insufficient_evidence",
]
ParticipantRoleLiteral = Literal[
    "author",
    "reviewer",
    "advocate",
    "objector",
    "maintainer",
    "commenter",
]

MAX_CANDIDATES = 5
MAX_TARGETS = 8
MAX_EVIDENCE_IDS = 12
MAX_PARTICIPANTS = 8


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateTarget(StrictModel):
    target_type: TargetTypeLiteral
    target_id: str = Field(min_length=1, max_length=500)

    @field_validator("target_id")
    @classmethod
    def _target_is_clean(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("target_id must not be blank")
        return value


class CandidateParticipant(StrictModel):
    login: str = Field(min_length=1, max_length=120)
    role: ParticipantRoleLiteral
    evidence_ids: list[str] = Field(default_factory=list, max_length=MAX_EVIDENCE_IDS)

    @field_validator("login")
    @classmethod
    def _login_is_clean(cls, value: str) -> str:
        value = value.strip().lstrip("@")
        if not value:
            raise ValueError("participant login must not be blank")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def _participant_evidence_is_clean(cls, value: list[str]) -> list[str]:
        return _clean_ids(value)


class TemporalScope(StrictModel):
    valid_from: str = Field(min_length=1, max_length=80)
    valid_until: str | None = Field(default=None, max_length=80)

    @field_validator("valid_from")
    @classmethod
    def _valid_from_is_clean(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("valid_from must not be blank")
        return value


class ExtractedCandidate(StrictModel):
    claim: str = Field(min_length=1, max_length=240)
    decision_type: DecisionTypeLiteral
    status: ExtractionStatusLiteral
    targets: list[CandidateTarget] = Field(max_length=MAX_TARGETS)
    evidence_ids: list[str] = Field(
        min_length=1, max_length=MAX_EVIDENCE_IDS
    )
    participants: list[CandidateParticipant] = Field(
        max_length=MAX_PARTICIPANTS
    )
    rationale: str = Field(max_length=800)
    temporal_scope: TemporalScope

    @field_validator("claim")
    @classmethod
    def _claim_is_one_sentence(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("claim must not be blank")
        return value

    @field_validator("rationale")
    @classmethod
    def _rationale_is_clean(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("evidence_ids")
    @classmethod
    def _evidence_is_clean(cls, value: list[str]) -> list[str]:
        return _clean_ids(value)

    @field_validator("targets")
    @classmethod
    def _targets_unique(
        cls, value: list[CandidateTarget]
    ) -> list[CandidateTarget]:
        seen: set[tuple[str, str]] = set()
        for target in value:
            key = (target.target_type, target.target_id)
            if key in seen:
                raise ValueError(
                    f"duplicate target {target.target_type}:{target.target_id}"
                )
            seen.add(key)
        return value

    @field_validator("participants")
    @classmethod
    def _participants_unique(
        cls, value: list[CandidateParticipant]
    ) -> list[CandidateParticipant]:
        seen: set[tuple[str, str]] = set()
        for participant in value:
            key = (participant.login, participant.role)
            if key in seen:
                raise ValueError(
                    f"duplicate participant {participant.login}:{participant.role}"
                )
            seen.add(key)
        return value


class CandidateExtraction(StrictModel):
    candidates: list[ExtractedCandidate] = Field(max_length=MAX_CANDIDATES)
    missing_evidence: list[str] = Field(max_length=8)

    @field_validator("missing_evidence")
    @classmethod
    def _missing_evidence_is_clean(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            item = " ".join(item.split())
            if not item:
                raise ValueError("missing_evidence entries must not be blank")
            if item not in cleaned:
                cleaned.append(item)
        return cleaned


# The plan calls this the "decision extraction schema"; the class name makes
# the unified candidate vocabulary explicit. Keep the descriptive alias for
# callers that refer to the phase by its deliverable name.
DecisionExtraction = CandidateExtraction


class SupersessionVerdict(StrictModel):
    """Phase 6's only supersession output shape.

    The model chooses the relationship; deterministic code owns the edge and
    the lifecycle update. ``insufficient_evidence`` and ``unrelated`` produce
    no edge at all.
    """

    earlier_decision_id: str = Field(min_length=1, max_length=500)
    later_decision_id: str = Field(min_length=1, max_length=500)
    relationship: SupersessionRelationshipLiteral
    evidence_ids: list[str] = Field(
        min_length=1, max_length=MAX_EVIDENCE_IDS
    )
    explanation: str = Field(max_length=800)

    @field_validator("evidence_ids")
    @classmethod
    def _verdict_evidence_is_clean(cls, value: list[str]) -> list[str]:
        return _clean_ids(value)

    @field_validator("explanation")
    @classmethod
    def _explanation_is_clean(cls, value: str) -> str:
        return " ".join(value.split())


class DuplicateVerdict(StrictModel):
    """Phase 7's only duplicate-adjudication output shape.

    The model decides whether an already-generated candidate pair is the same
    decision, two separate decisions, conflicting, or not decidable. It never
    writes a merged claim, chooses a canonical ID, or creates an edge.
    """

    earlier_intent_id: str = Field(min_length=1, max_length=500)
    later_intent_id: str = Field(min_length=1, max_length=500)
    relationship: DuplicateRelationshipLiteral
    evidence_ids: list[str] = Field(
        min_length=1, max_length=MAX_EVIDENCE_IDS
    )
    explanation: str = Field(max_length=800)

    @field_validator("evidence_ids")
    @classmethod
    def _verdict_evidence_is_clean(cls, value: list[str]) -> list[str]:
        return _clean_ids(value)

    @field_validator("explanation")
    @classmethod
    def _explanation_is_clean(cls, value: str) -> str:
        return " ".join(value.split())


SCHEMA_NAMES: dict[type[BaseModel], str] = {
    CandidateExtraction: "decision_extraction",
    SupersessionVerdict: "supersession_verdict",
    DuplicateVerdict: "duplicate_verdict",
}


def _clean_ids(value: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for evidence_id in value:
        candidate = evidence_id.strip()
        if not candidate:
            raise ValueError("evidence id must not be blank")
        if any(character.isspace() for character in candidate):
            raise ValueError(f"evidence id contains whitespace: {candidate!r}")
        if candidate in seen:
            raise ValueError(f"duplicate evidence id {candidate!r}")
        seen.add(candidate)
        cleaned.append(candidate)
    return cleaned


__all__ = [
    "CandidateExtraction",
    "CandidateParticipant",
    "CandidateTarget",
    "DecisionExtraction",
    "DecisionTypeLiteral",
    "DuplicateRelationshipLiteral",
    "DuplicateVerdict",
    "ExtractedCandidate",
    "ExtractionStatusLiteral",
    "MAX_CANDIDATES",
    "MAX_EVIDENCE_IDS",
    "MAX_PARTICIPANTS",
    "MAX_TARGETS",
    "ParticipantRoleLiteral",
    "SCHEMA_NAMES",
    "StrictModel",
    "SupersessionRelationshipLiteral",
    "SupersessionVerdict",
    "TargetTypeLiteral",
    "TemporalScope",
]
