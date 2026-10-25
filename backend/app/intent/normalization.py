"""Proposal normalisation.

Validated candidates become canonical intents here. Identity, produced-in
links and confidence are computed from accepted evidence; only the claim,
rationale, classification and target choices remain the model's
contribution.
"""

from __future__ import annotations

from collections.abc import Mapping

from app.intent.config import IntentConfig
from app.intent.ids import evidence_node_id, intent_id
from app.intent.llm.schemas import ExtractedCandidate
from app.intent.models import (
    ConfidenceResult,
    ConstraintIntent,
    DecisionIntent,
    DecisionStatus,
    DecisionType,
    EvidenceRef,
    ExtractionStatus,
    IntentBase,
    IntentKind,
    ParticipantRole,
    ProposedParticipantRef,
    ResolvedTarget,
    ReversalIntent,
    TemporalScope,
)
from app.intent.selection.evidence_selector import PrEvidenceBundle
from app.intent.validation.graph_link_validator import GraphIndex, TargetValidation

_EXCERPT_LIMIT = 400
_CONSTRAINT_TYPES = {"constraint"}
_REVERSAL_TYPES = {"reversal"}


class IntentNormalizer:
    def __init__(self, config: IntentConfig) -> None:
        self.config = config

    def candidate(
        self,
        proposal: ExtractedCandidate,
        *,
        bundle: PrEvidenceBundle,
        evidence_ids: list[str],
        target_validation: TargetValidation,
        confidence: ConfidenceResult,
        index: GraphIndex,
        source_candidate: str,
        missing_evidence: list[str] | None = None,
    ) -> IntentBase:
        common = {
            "repo": bundle.repo,
            "pr_number": bundle.pr_number,
            "claim": proposal.claim,
            "rationale": proposal.rationale,
            "targets": target_validation.targets,
            "targets_proposed": _proposed_targets(proposal),
            "targets_dropped": target_validation.dropped,
            "evidence_ids": evidence_ids,
            "produced_in": _produced_in(bundle, evidence_ids, index),
            "confidence": confidence.level,
            "confidence_reasons": confidence.reasons,
            "confidence_score": confidence.score,
            "extracted_by": self.config.model,
            "extraction_version": self.config.extraction_version,
            "missing_evidence": list(missing_evidence or []),
            "source_candidate": source_candidate,
            "decision_type": DecisionType(proposal.decision_type),
            "extraction_status": ExtractionStatus(proposal.status),
            "temporal_scope": _temporal_scope(proposal),
            "participants_proposed": _participants(proposal),
            "status": _canonical_status(proposal.status),
        }
        if proposal.decision_type in _CONSTRAINT_TYPES:
            return ConstraintIntent(
                id=intent_id(
                    "con",
                    repo=bundle.repo,
                    claim=proposal.claim,
                    evidence_ids=evidence_ids,
                ),
                kind=IntentKind.CONSTRAINT,
                constraint_type="other",
                **common,  # type: ignore[arg-type]
            )
        if proposal.decision_type in _REVERSAL_TYPES:
            return ReversalIntent(
                id=intent_id(
                    "rev",
                    repo=bundle.repo,
                    claim=proposal.claim,
                    evidence_ids=evidence_ids,
                ),
                kind=IntentKind.REVERSAL,
                reversal_type="revert",
                reversed_claim="",
                **common,  # type: ignore[arg-type]
            )
        return DecisionIntent(
            id=intent_id(
                "dec",
                repo=bundle.repo,
                claim=proposal.claim,
                evidence_ids=evidence_ids,
            ),
            kind=IntentKind.DECISION,
            proposed_id=None,
            **common,  # type: ignore[arg-type]
        )


def build_evidence_ref(
    evidence_id: str, bundle: PrEvidenceBundle
) -> EvidenceRef:
    item = bundle.items_by_id().get(evidence_id)
    if item is not None:
        payload = item.payload
        if item.record_type == "changed_file":
            excerpt = _payload_text(payload.get("patch")) or ""
        else:
            excerpt = item.text
        return EvidenceRef(
            evidence_id=evidence_id,
            node_id=evidence_node_id(repo=bundle.repo, evidence_id=evidence_id),
            record_type=item.record_type,
            source_record_id=item.record_id,
            pr_number=item.pr_number,
            issue_number=_issue_number(item.record_type, payload),
            commit_sha=_payload_text(payload.get("sha")),
            author=item.author,
            occurred_at=item.occurred_at or None,
            path=_payload_text(payload.get("path")),
            start_line=_positive_int(payload.get("start_line")),
            end_line=_positive_int(payload.get("line")),
            excerpt=excerpt[:_EXCERPT_LIMIT],
            url=item.url,
            content_hash=item.content_hash,
        )
    return EvidenceRef(
        evidence_id=evidence_id,
        node_id=evidence_node_id(repo=bundle.repo, evidence_id=evidence_id),
        record_type=_record_type_from_id(evidence_id),
        source_record_id=evidence_id,
        pr_number=bundle.pr_number,
    )


def _payload_text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _issue_number(
    record_type: str, payload: Mapping[str, object]
) -> int | None:
    value = payload.get("issue_number")
    if value is None and record_type == "issue":
        value = payload.get("number")
    return value if isinstance(value, int) and value >= 0 else None


def _proposed_targets(proposal: ExtractedCandidate) -> list[str]:
    return [
        f"{target.target_type}:{target.target_id}"
        for target in proposal.targets
    ]


def _participants(proposal: ExtractedCandidate) -> list[ProposedParticipantRef]:
    return [
        ProposedParticipantRef(
            login=participant.login,
            role=ParticipantRole(participant.role),
            evidence_ids=list(participant.evidence_ids),
        )
        for participant in proposal.participants
    ]


def _temporal_scope(proposal: ExtractedCandidate) -> TemporalScope:
    return TemporalScope(
        valid_from=proposal.temporal_scope.valid_from,
        valid_until=proposal.temporal_scope.valid_until,
    )


def _canonical_status(status: str) -> DecisionStatus:
    if status == "rejected":
        return DecisionStatus.REJECTED
    if status == "reversed":
        return DecisionStatus.REVERSED
    if status == "uncertain":
        return DecisionStatus.UNCERTAIN
    return DecisionStatus.ACTIVE


def _produced_in(
    bundle: PrEvidenceBundle,
    evidence_ids: list[str],
    index: GraphIndex,
) -> list[str]:
    produced: list[str] = []
    if index.has_node(bundle.pr.record_id):
        produced.append(bundle.pr.record_id)
    by_id = bundle.items_by_id()
    for evidence_id in evidence_ids:
        item = by_id.get(evidence_id)
        if item is None or item.record_type != "commit":
            continue
        if index.has_node(item.record_id):
            produced.append(item.record_id)
    return sorted(set(produced))


def _record_type_from_id(evidence_id: str) -> str:
    lowered = evidence_id.lower()
    for token, record_type in (
        (":pr:", "pull_request"),
        (":commit:", "commit"),
        (":review_comment:", "review_comment"),
        (":review:", "review"),
        (":issue_comment:", "issue_comment"),
        (":issue:", "issue"),
        (":person:", "person"),
        (":doc:", "doc_file"),
        (":region:", "code_region"),
    ):
        if token in lowered:
            return record_type
    return "unknown"


def resolved_target_summary(targets: list[ResolvedTarget]) -> list[str]:
    return [target.graph_key for target in targets]


__all__ = [
    "IntentNormalizer",
    "build_evidence_ref",
    "resolved_target_summary",
]
