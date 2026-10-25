"""Explicit refusal behavior.

Refusal is the product's safety property, not an error path. Each mode has
its own message so the UI and the judge can tell the difference between
"unknown entity" and "entity exists, no decision trail".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.retrieval.context.evidence_packer import EvidencePack
from app.retrieval.models import (
    AnswerConfidence,
    AnswerStatus,
    DecisionCandidate,
    DecisionState,
    GraphContext,
    NearestCandidate,
    QuestionUnderstanding,
    RefusalMode,
    RefusalResult,
)

NO_ENTITY_MESSAGE = "I could not identify a relevant code entity or decision."
NO_DECISION_MESSAGE = (
    "The graph contains the code entity but no defensible decision trail."
)
SUPERSEDED_MESSAGE = (
    "The ingested history contains only decisions that were later superseded "
    "or reversed, so I will not present them as current truth."
)
CONTESTED_MESSAGE = (
    "The ingested history contains conflicting decisions, so I cannot present "
    "one of them as the current answer."
)
VALIDATION_MESSAGE = (
    "I cannot produce an answer whose claims are fully supported by the "
    "ingested decision evidence."
)

_HISTORICAL = {DecisionState.SUPERSEDED, DecisionState.REVERSED}


class RefusalHandler:
    def decide(
        self,
        *,
        understanding: QuestionUnderstanding,
        candidates: Sequence[DecisionCandidate],
        contexts: Mapping[str, GraphContext],
    ) -> RefusalResult | None:
        nearest = _nearest(candidates)
        if not candidates:
            if not understanding.entities:
                return RefusalResult(
                    mode=RefusalMode.NO_ENTITY,
                    answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                    answer=NO_ENTITY_MESSAGE,
                    nearest_candidates=nearest,
                )
            return RefusalResult(
                mode=RefusalMode.NO_DECISION,
                answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                answer=NO_DECISION_MESSAGE,
                nearest_candidates=nearest,
            )

        if not any(
            contexts.get(candidate.decision_id) is not None
            and contexts[candidate.decision_id].evidence
            for candidate in candidates
        ):
            return RefusalResult(
                mode=RefusalMode.NO_DECISION,
                answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
                answer=NO_DECISION_MESSAGE,
                nearest_candidates=nearest,
            )

        if all(_is_historical(candidate, contexts) for candidate in candidates):
            return self.superseded_only(candidates=candidates, contexts=contexts)
        return None

    def superseded_only(
        self,
        *,
        candidates: Sequence[DecisionCandidate],
        contexts: Mapping[str, GraphContext],
    ) -> RefusalResult:
        """Explain the reversal instead of presenting the old claim as truth."""
        parts = [SUPERSEDED_MESSAGE]
        later_seen: list[str] = []
        for candidate in candidates:
            later = _later_ids(candidate, contexts)
            for later_id in later:
                if later_id not in later_seen:
                    later_seen.append(later_id)
            sentence = f"Historical decision {candidate.decision_id}"
            claim = " ".join(candidate.claim.split())
            if claim:
                sentence += f' ("{claim}")'
            if later:
                sentence += " was superseded or reversed by " + ", ".join(later)
            else:
                sentence += " was later superseded or reversed"
            parts.append(sentence + ".")
        if later_seen:
            parts.append(
                "Answer from the superseding decision instead; it is not part "
                "of the selected evidence for this question."
            )
        caveats = [
            "Only superseded or reversed decisions matched the question; the "
            "current decision was not selected."
        ]
        return RefusalResult(
            mode=RefusalMode.SUPERSEDED_ONLY,
            answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
            answer=" ".join(parts),
            confidence=AnswerConfidence.NONE,
            decision_ids=[candidate.decision_id for candidate in candidates],
            caveats=caveats,
            nearest_candidates=_nearest(candidates),
        )

    def is_contested(
        self,
        *,
        candidates: Sequence[DecisionCandidate],
        contexts: Mapping[str, GraphContext],
    ) -> bool:
        if any(candidate.status is DecisionState.CONTESTED for candidate in candidates):
            return True
        return any(
            contexts[candidate.decision_id].conflicts
            for candidate in candidates
            if candidate.decision_id in contexts
        )

    def contested(self, pack: EvidencePack) -> RefusalResult:
        parts = [CONTESTED_MESSAGE]
        citations: list[str] = []
        for alias, decision_id in pack.decision_alias_map.items():
            decision = next(
                (item for item in pack.decisions if item.decision_id == decision_id),
                None,
            )
            if decision is None:
                continue
            evidence_id = next(
                (
                    item.evidence_id
                    for item in pack.evidence
                    if item.decision_id == decision_id
                ),
                None,
            )
            evidence_alias = pack.alias_for(evidence_id) if evidence_id else None
            citation = f" [{evidence_alias}]" if evidence_alias else ""
            parts.append(f"[{alias}] {decision.claim}{citation}.")
            if evidence_id:
                citations.append(evidence_id)
        return RefusalResult(
            mode=RefusalMode.CONTESTED,
            answer_status=AnswerStatus.CONTESTED,
            answer=" ".join(parts),
            confidence=AnswerConfidence.LOW,
            decision_ids=list(pack.decision_alias_map.values()),
            evidence_ids=[item.evidence_id for item in pack.evidence],
            caveats=[
                "The selected decisions conflict; both readings are shown with evidence."
            ],
            citations=list(dict.fromkeys(citations)),
        )

    def insufficient(
        self,
        *,
        candidates: Sequence[DecisionCandidate],
        caveats: Sequence[str] = (),
    ) -> RefusalResult:
        return RefusalResult(
            mode=RefusalMode.NO_DECISION,
            answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
            answer=VALIDATION_MESSAGE,
            decision_ids=[],
            evidence_ids=[],
            caveats=list(caveats),
            nearest_candidates=_nearest(candidates),
        )


def _later_ids(
    candidate: DecisionCandidate, contexts: Mapping[str, GraphContext]
) -> list[str]:
    ids: list[str] = []
    if candidate.superseded_by:
        ids.append(candidate.superseded_by)
    context = contexts.get(candidate.decision_id)
    if context is not None:
        for later_id in context.later_decisions:
            if later_id not in ids:
                ids.append(later_id)
    return ids


def _is_historical(
    candidate: DecisionCandidate, contexts: Mapping[str, GraphContext]
) -> bool:
    if candidate.status in _HISTORICAL:
        return True
    context = contexts.get(candidate.decision_id)
    return bool(context and context.later_decisions)


def _nearest(candidates: Sequence[DecisionCandidate]) -> list[NearestCandidate]:
    return [
        NearestCandidate(
            decision_id=candidate.decision_id,
            claim=candidate.claim,
            status=candidate.status,
        )
        for candidate in candidates[:3]
    ]


__all__ = [
    "CONTESTED_MESSAGE",
    "NO_DECISION_MESSAGE",
    "NO_ENTITY_MESSAGE",
    "RefusalHandler",
    "SUPERSEDED_MESSAGE",
    "VALIDATION_MESSAGE",
]
