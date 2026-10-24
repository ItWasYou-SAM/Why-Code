"""Golden-case and prediction shapes for the Phase 10 evaluation set.

The evaluation set is deliberately small and hand-curated (D-0048). A case is
not allowed to enter the suite without a located artifact: the expected
evidence IDs point at records that exist in the ingestion store, and the
expected targets use the same ``type:identifier`` form the extraction pipeline
resolves against the graph.

Prediction shapes are intentionally permissive at the boundary. The evaluator
must be able to score an ``intent_batch.json`` produced by the real pipeline as
well as a hand-written prediction file, without asking either producer to
change its output contract.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CaseStatus = Literal["accepted", "reversed", "contested", "uncertain", "refused"]
SupersessionRelationship = Literal["reverses", "revises", "supersedes"]
TargetKind = Literal["symbol", "config_key", "file", "code_region"]

TARGET_KINDS: frozenset[str] = frozenset(
    {"symbol", "config_key", "file", "code_region"}
)
_CASE_ID = re.compile(r"^[a-z0-9][a-z0-9_]*$")
_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def normalize_target_type(value: str) -> str:
    """Accept both the model-facing ``ConfigKey`` and the golden ``config_key``."""

    return _CAMEL_BOUNDARY.sub("_", value.strip()).lower()


def normalize_golden_target(value: str) -> str:
    """Normalise a ``type:identifier`` target string.

    The type is lower-cased and must be one of the four graph target kinds. The
    identifier is preserved; matching applies its own path/config normalisation.
    """

    kind, separator, identifier = value.partition(":")
    if not separator:
        raise ValueError(f"target {value!r} must use 'type:identifier'")
    kind = kind.strip().lower()
    identifier = identifier.strip()
    if kind not in TARGET_KINDS:
        raise ValueError(f"unknown target type {kind!r} in {value!r}")
    if not identifier:
        raise ValueError(f"target {value!r} has an empty identifier")
    return f"{kind}:{identifier}"


class GoldenSupersession(BaseModel):
    """The reversal edge a manually adjudicated later decision must create."""

    model_config = ConfigDict(extra="forbid")

    relationship: SupersessionRelationship
    target_case_id: str = Field(min_length=1)


class GoldenCase(BaseModel):
    """One manually verified decision in the golden set."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    corpus: str = Field(min_length=1)
    question: str = Field(min_length=1)
    expected_decision: str | None = None
    expected_targets: list[str] = Field(default_factory=list)
    expected_evidence: list[str] = Field(default_factory=list)
    expected_prs: list[int] = Field(min_length=1)
    expected_status: CaseStatus = "accepted"
    expected_supersession: GoldenSupersession | None = None
    notes: str = ""

    @field_validator("case_id")
    @classmethod
    def _case_id_is_slug(cls, value: str) -> str:
        value = value.strip()
        if not _CASE_ID.match(value):
            raise ValueError("case_id must be a lower-case slug")
        return value

    @field_validator("expected_targets")
    @classmethod
    def _targets_are_canonical(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            target = normalize_golden_target(item)
            if target in cleaned:
                raise ValueError(f"duplicate target {target!r}")
            cleaned.append(target)
        return cleaned

    @field_validator("expected_evidence")
    @classmethod
    def _evidence_is_unique(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            item = item.strip()
            if not item:
                raise ValueError("expected evidence IDs must be non-empty")
            if item in cleaned:
                raise ValueError(f"duplicate evidence ID {item!r}")
            cleaned.append(item)
        return cleaned

    @field_validator("expected_prs")
    @classmethod
    def _prs_are_unique(cls, value: list[int]) -> list[int]:
        if any(pr <= 0 for pr in value):
            raise ValueError("expected PR numbers must be positive")
        if len(set(value)) != len(value):
            raise ValueError("expected PR numbers must be unique")
        return value

    @model_validator(mode="after")
    def _shape_matches_status(self) -> GoldenCase:
        if self.expected_status == "refused":
            if self.expected_decision is not None:
                raise ValueError("a refused case cannot have an expected decision")
            if self.expected_targets or self.expected_evidence:
                raise ValueError("a refused case cannot have targets or evidence")
            if self.expected_supersession is not None:
                raise ValueError("a refused case cannot have a supersession edge")
        else:
            if not self.expected_decision or not self.expected_decision.strip():
                raise ValueError(
                    "an accepted/uncertain case must have an expected decision"
                )
        return self


class PredictedTarget(BaseModel):
    """A target emitted by a run, normalised to ``type:identifier`` form."""

    model_config = ConfigDict(extra="ignore")

    type: str = Field(min_length=1)
    identifier: str = Field(min_length=1)

    @model_validator(mode="after")
    def _normalise(self) -> PredictedTarget:
        kind = normalize_target_type(self.type)
        if kind not in TARGET_KINDS:
            raise ValueError(f"unknown predicted target type {self.type!r}")
        self.type = kind
        self.identifier = self.identifier.strip()
        if not self.identifier:
            raise ValueError("predicted target identifier must not be blank")
        return self

    def as_key(self) -> str:
        return f"{self.type}:{self.identifier}"


class PredictedDecision(BaseModel):
    """One decision-shaped output from an extraction run."""

    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    pr_number: int | None = None
    claim: str = Field(min_length=1)
    kind: str = "decision"
    status: str = "active"
    evidence_ids: list[str] = Field(default_factory=list)
    targets: list[PredictedTarget] = Field(default_factory=list)


class PredictedEdge(BaseModel):
    """One supersession edge from a run (later node -> earlier node)."""

    model_config = ConfigDict(extra="ignore")

    rel_type: str = Field(min_length=1)
    start_id: str = Field(min_length=1)
    end_id: str = Field(min_length=1)

    @field_validator("rel_type")
    @classmethod
    def _relationship_is_lower(cls, value: str) -> str:
        return value.strip().lower()


class PredictionSet(BaseModel):
    """Everything the evaluator needs to score a run."""

    model_config = ConfigDict(extra="forbid")

    decisions: list[PredictedDecision] = Field(default_factory=list)
    edges: list[PredictedEdge] = Field(default_factory=list)
    refusals: set[int] = Field(default_factory=set)
    refusals_provided: bool = False

    @model_validator(mode="after")
    def _ids_are_unique(self) -> PredictionSet:
        ids = [decision.id for decision in self.decisions]
        if len(set(ids)) != len(ids):
            raise ValueError("predicted decision IDs must be unique")
        return self


__all__ = [
    "CaseStatus",
    "GoldenCase",
    "GoldenSupersession",
    "PredictedDecision",
    "PredictedEdge",
    "PredictedTarget",
    "PredictionSet",
    "SupersessionRelationship",
    "TargetKind",
    "TARGET_KINDS",
    "normalize_golden_target",
    "normalize_target_type",
]
