"""Evidence existence and provenance validation.

A citation is accepted only when it is:

1. present in the retrieved evidence pack (the model saw it),
2. present in the SQLite ingestion store, and
3. present in the graph that will receive the edge.

This is intentionally stricter than "the ID merely looks plausible". A
whole proposal is rejected if any citation fails; valid-looking claims with
one bad citation are exactly the failure this component exists to stop.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.records import EvidenceRecord
from app.intent.llm.schemas import ExtractedCandidate
from app.intent.models import (
    Confidence,
    ConfidenceResult,
    DecisionStatus,
    ExtractionStatus,
)
from app.intent.selection.evidence_selector import EvidenceItem, PrEvidenceBundle
from app.intent.validation.claim_grounding import ClaimGrounder, GroundingResult
from app.intent.validation.confidence_scorer import ConfidenceScorer
from app.intent.validation.graph_link_validator import TargetValidation
from app.intent.validation.participant_validation import (
    ParticipantValidation,
    ParticipantValidator,
)


class EvidenceLookup(Protocol):
    def get_record(self, record_id: str) -> EvidenceRecord | None: ...


@dataclass(frozen=True)
class EvidenceIssue:
    code: str
    evidence_id: str
    message: str


@dataclass
class EvidenceValidation:
    ok: bool
    accepted_ids: list[str] = field(default_factory=list)
    issues: list[EvidenceIssue] = field(default_factory=list)

    @property
    def issue_texts(self) -> list[str]:
        return [f"{issue.code}: {issue.message}" for issue in self.issues]


class EvidenceValidator:
    def __init__(
        self,
        store: EvidenceLookup,
        *,
        require_graph: bool = True,
    ) -> None:
        self.store = store
        self.require_graph = require_graph
        self.grounder = ClaimGrounder()
        self.participant_validator = ParticipantValidator()
        self.confidence = ConfidenceScorer()

    def validate(
        self,
        evidence_ids: list[str],
        *,
        allowed_ids: set[str],
        graph_evidence_ids: set[str] | None = None,
    ) -> EvidenceValidation:
        issues: list[EvidenceIssue] = []
        accepted: list[str] = []
        if not evidence_ids:
            return EvidenceValidation(
                ok=False,
                issues=[
                    EvidenceIssue(
                        "no_evidence",
                        "",
                        "an intent node cannot be created without evidence",
                    )
                ],
            )

        seen: set[str] = set()
        for evidence_id in evidence_ids:
            if evidence_id in seen:
                issues.append(
                    EvidenceIssue(
                        "duplicate_evidence", evidence_id, "duplicate citation"
                    )
                )
                continue
            seen.add(evidence_id)
            if evidence_id not in allowed_ids:
                issues.append(
                    EvidenceIssue(
                        "evidence_outside_pack",
                        evidence_id,
                        "evidence ID was not included in the retrieved evidence pack",
                    )
                )
                continue
            if self.store.get_record(evidence_id) is None:
                issues.append(
                    EvidenceIssue(
                        "evidence_not_found",
                        evidence_id,
                        "evidence ID does not exist in the ingestion store",
                    )
                )
                continue
            if self.require_graph:
                if graph_evidence_ids is None:
                    issues.append(
                        EvidenceIssue(
                            "graph_index_unavailable",
                            evidence_id,
                            "cannot verify the evidence node exists in the graph",
                        )
                    )
                    continue
                if evidence_id not in graph_evidence_ids:
                    issues.append(
                        EvidenceIssue(
                            "evidence_missing_from_graph",
                            evidence_id,
                            "evidence ID is absent from the loaded graph",
                        )
                    )
                    continue
            accepted.append(evidence_id)

        return EvidenceValidation(
            ok=not issues, accepted_ids=accepted if not issues else [], issues=issues
        )

    # -- Phase 4: candidate-level firewall -------------------------------
    def validate_candidate(
        self,
        proposal: ExtractedCandidate | dict[str, Any],
        *,
        bundle: PrEvidenceBundle,
        allowed_ids: set[str],
        graph_evidence_ids: set[str] | None,
        target_validation: TargetValidation,
        decision_id: str = "",
        resolve_citation: Callable[[str], str] | None = None,
        allow_target_downgrade: bool = True,
        reject_unsupported_participants: bool = False,
    ) -> CandidateValidation:
        """Run the complete Phase 4 gate over one extracted candidate.

        The result is deliberately auditable: ``EvidenceValidationResult`` is
        the stable public JSON shape, while ``CandidateValidation`` carries the
        evidence/target/grounding detail the pipeline needs to make a decision.
        """
        if isinstance(proposal, dict):
            try:
                proposal = ExtractedCandidate.model_validate(proposal)
            except ValidationError as error:
                issues = [
                    f"schema: {'.'.join(str(part) for part in item.get('loc', ())) or '<root>'}: "
                    f"{item.get('msg', 'invalid value')}"
                    for item in error.errors()[:10]
                ]
                return _schema_failure(decision_id, issues)

        resolver = resolve_citation or (lambda value: value)
        resolved_ids = [resolver(value) for value in proposal.evidence_ids]
        evidence = self.validate(
            resolved_ids,
            allowed_ids=allowed_ids,
            graph_evidence_ids=graph_evidence_ids,
        )
        evidence_items = [
            item
            for evidence_id in evidence.accepted_ids
            if (item := bundle.items_by_id().get(evidence_id)) is not None
        ]

        if evidence.ok:
            grounding = self.grounder.ground(
                proposal.claim,
                proposal.rationale,
                evidence_items,
                target_paths=[
                    target.path
                    for target in target_validation.targets
                    if target.path
                ],
                valid_from=proposal.temporal_scope.valid_from,
                valid_until=proposal.temporal_scope.valid_until,
                decision_type=proposal.decision_type,
                reference_items=[bundle.pr],
            )
            participants = self.participant_validator.validate(
                proposal.participants,
                bundle=bundle,
                evidence_items=evidence_items,
                resolve_citation=resolver,
            )
        else:
            # A citation failure rejects the whole candidate. There is no
            # trustworthy text to ground against, so the result reports only
            # the invalid IDs rather than a cascade of unsupported claims.
            grounding = GroundingResult(ok=False)
            participants = ParticipantValidation()

        reason_codes: list[str] = [issue.code for issue in evidence.issues]
        notes: list[str] = []
        unsupported = [
            *grounding.unsupported_claims,
            *grounding.temporal_issues,
            *grounding.reversal_issues,
        ]

        if not proposal.targets:
            reason_codes.append("no_structural_target")
            notes.append(
                "a decision without a structural target cannot be linked to code"
            )
        elif not target_validation.targets:
            reason_codes.append("no_structural_target")
            notes.append("no proposed target exists in the loaded graph")
        elif target_validation.issues:
            reason_codes.append("target_partially_unresolved")
            notes.extend(target_validation.issues)

        if grounding.unsupported_claims:
            reason_codes.append("unsupported_claim")
        if grounding.temporal_issues:
            reason_codes.append("temporal_invalid")
        if grounding.reversal_issues:
            reason_codes.append("reversal_without_later_change")
        if grounding.conflict:
            reason_codes.append("conflicting_evidence")
            notes.append(
                "cited evidence conflicts; the decision is marked contested "
                "instead of being silently resolved"
            )
        if grounding.irrelevant_evidence_ids:
            notes.append(
                "irrelevant citations: "
                + ", ".join(grounding.irrelevant_evidence_ids)
            )
        if participants.issues:
            reason_codes.append("participant_unsupported")
            notes.extend(participants.issues)

        targets_valid = bool(target_validation.targets) and not target_validation.issues
        final_status = self._final_status(
            evidence=evidence,
            proposal=proposal,
            target_validation=target_validation,
            unsupported=unsupported,
            participants=participants,
            allow_target_downgrade=allow_target_downgrade,
            reject_unsupported_participants=reject_unsupported_participants,
        )
        canonical_status: DecisionStatus | None = None
        extraction_status: ExtractionStatus | None = None
        if final_status is FinalStatus.OBSERVATION:
            canonical_status = DecisionStatus.UNCERTAIN
            extraction_status = ExtractionStatus.UNCERTAIN
            notes.append(
                "some targets were unresolved; the decision is downgraded to "
                "an observation"
            )
        if grounding.conflict and final_status in {
            FinalStatus.ACCEPTED_FOR_GRAPH,
            FinalStatus.OBSERVATION,
        }:
            canonical_status = DecisionStatus.CONTESTED

        # Phase 5: confidence is computed last, from the evidence that survived
        # grounding, so an observation's ``uncertain`` status can discount it.
        confidence = self._confidence(
            grounding,
            evidence_items,
            status=(
                canonical_status.value
                if canonical_status is not None
                else proposal.status
            ),
        )

        result = EvidenceValidationResult(
            decision_id=decision_id,
            schema_valid=True,
            evidence_valid=evidence.ok,
            targets_valid=targets_valid,
            unsupported_claims=unsupported,
            invalid_evidence_ids=_unique(
                issue.evidence_id
                for issue in evidence.issues
                if issue.evidence_id
            ),
            invalid_target_ids=_unique(target_validation.dropped),
            final_status=final_status,
        )
        return CandidateValidation(
            result=result,
            evidence=evidence,
            targets=target_validation,
            grounding=grounding,
            confidence=confidence,
            participant_validation=participants,
            canonical_status=canonical_status,
            extraction_status=extraction_status,
            reason_codes=_unique(reason_codes),
            notes=_unique(notes),
        )

    def validate_decision(
        self,
        proposal: ExtractedCandidate | dict[str, Any],
        **kwargs: Any,
    ) -> CandidateValidation:
        """Alias used by callers that name the extraction output a decision."""
        return self.validate_candidate(proposal, **kwargs)

    def _confidence(
        self,
        grounding: GroundingResult,
        evidence_items: list[EvidenceItem],
        *,
        status: str | None = None,
    ) -> ConfidenceResult:
        supporting_ids = set(grounding.supporting_evidence_ids)
        relevant_ids = {
            item.evidence_id for item in grounding.evidence if item.relevant
        }
        supporting = [
            item
            for item in evidence_items
            if item.record_id in supporting_ids and item.record_id in relevant_ids
        ]
        if not supporting:
            return ConfidenceResult(
                level=Confidence.INSUFFICIENT,
                score=0.0,
                reasons=["no cited evidence supports the claim text"],
            )
        return self.confidence.score(supporting, status=status)

    @staticmethod
    def _final_status(
        *,
        evidence: EvidenceValidation,
        proposal: ExtractedCandidate,
        target_validation: TargetValidation,
        unsupported: list[str],
        participants: ParticipantValidation,
        allow_target_downgrade: bool,
        reject_unsupported_participants: bool,
    ) -> FinalStatus:
        if not evidence.ok:
            return FinalStatus.REJECTED
        if not proposal.targets or not target_validation.targets:
            return FinalStatus.REJECTED
        partial_targets = bool(target_validation.issues)
        if partial_targets and not allow_target_downgrade:
            return FinalStatus.REJECTED
        if unsupported:
            return FinalStatus.NEEDS_REVIEW
        if reject_unsupported_participants and participants.rejected:
            return FinalStatus.NEEDS_REVIEW
        if partial_targets:
            return FinalStatus.OBSERVATION
        return FinalStatus.ACCEPTED_FOR_GRAPH


class FinalStatus(StrEnum):
    ACCEPTED_FOR_GRAPH = "accepted_for_graph"
    OBSERVATION = "observation"
    NEEDS_REVIEW = "needs_review"
    REJECTED = "rejected"
    DISCARDED = "discarded"


class EvidenceValidationResult(BaseModel):
    """The stable Phase 4 result shape written beside each candidate."""

    model_config = ConfigDict(extra="forbid")

    decision_id: str = ""
    schema_valid: bool
    evidence_valid: bool
    targets_valid: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    invalid_evidence_ids: list[str] = Field(default_factory=list)
    invalid_target_ids: list[str] = Field(default_factory=list)
    final_status: FinalStatus


@dataclass
class CandidateValidation:
    result: EvidenceValidationResult
    evidence: EvidenceValidation = field(
        default_factory=lambda: EvidenceValidation(ok=False)
    )
    targets: TargetValidation = field(default_factory=TargetValidation)
    grounding: GroundingResult | None = None
    confidence: ConfidenceResult = field(
        default_factory=lambda: ConfidenceResult(
            level=Confidence.INSUFFICIENT
        )
    )
    participant_validation: ParticipantValidation = field(
        default_factory=ParticipantValidation
    )
    canonical_status: DecisionStatus | None = None
    extraction_status: ExtractionStatus | None = None
    reason_codes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.result.final_status in {
            FinalStatus.ACCEPTED_FOR_GRAPH,
            FinalStatus.OBSERVATION,
        }

    @property
    def issue_texts(self) -> list[str]:
        texts = list(self.result.unsupported_claims)
        if self.result.invalid_evidence_ids:
            texts.append(
                "invalid evidence: "
                + ", ".join(self.result.invalid_evidence_ids)
            )
        if self.result.invalid_target_ids:
            texts.append(
                "invalid targets: " + ", ".join(self.result.invalid_target_ids)
            )
        texts.extend(self.targets.issues)
        texts.extend(self.notes)
        return _unique(texts)


def _schema_failure(
    decision_id: str,
    issues: list[str],
) -> CandidateValidation:
    """A payload that does not validate never reaches the evidence gates."""

    return CandidateValidation(
        result=EvidenceValidationResult(
            decision_id=decision_id,
            schema_valid=False,
            evidence_valid=False,
            targets_valid=False,
            unsupported_claims=issues,
            final_status=FinalStatus.DISCARDED,
        ),
        reason_codes=["schema_invalid"],
        notes=issues,
    )


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        unique.append(value)
    return unique


__all__ = [
    "CandidateValidation",
    "EvidenceIssue",
    "EvidenceLookup",
    "EvidenceValidation",
    "EvidenceValidationResult",
    "EvidenceValidator",
    "FinalStatus",
]
