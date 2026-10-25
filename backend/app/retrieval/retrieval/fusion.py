"""Weighted reciprocal rank fusion (WRRF).

Cosine similarity and BM25 are not comparable. This module never sees
either: each source contributes only ``weight / (k + rank)``. A decision
that appears in several independently ranked lists therefore wins on
agreement, not on the scale of some backend's score.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.retrieval.config import RetrievalConfig
from app.retrieval.models import (
    AnswerConfidence,
    DecisionCandidate,
    DecisionState,
    FusedCandidate,
    RetrievalSource,
)

_WEIGHTS: dict[RetrievalSource, str] = {
    RetrievalSource.EXACT: "exact_weight",
    RetrievalSource.FULLTEXT: "fulltext_weight",
    RetrievalSource.VECTOR: "vector_weight",
    RetrievalSource.GRAPH: "graph_weight",
}

_STATUS_PRIORITY = {
    DecisionState.ACTIVE: 0,
    DecisionState.UNCERTAIN: 1,
    DecisionState.REJECTED: 2,
    DecisionState.REVERSED: 3,
    DecisionState.SUPERSEDED: 4,
    DecisionState.CONTESTED: 5,
}


class Fusion:
    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig()

    def fuse(
        self,
        results: Mapping[RetrievalSource, Sequence[DecisionCandidate]],
    ) -> list[FusedCandidate]:
        merged: dict[str, DecisionCandidate] = {}
        scores: dict[str, float] = {}
        contributions: dict[str, dict[RetrievalSource, float]] = {}

        for source, candidates in results.items():
            weight = getattr(self.config, _WEIGHTS[source])
            seen_in_source: set[str] = set()
            accepted = 0
            for candidate in candidates:
                if candidate.decision_id in seen_in_source:
                    continue
                seen_in_source.add(candidate.decision_id)
                accepted += 1
                rank = max(1, candidate.sources.get(source, accepted))
                contribution = weight / (self.config.k + rank)
                existing = merged.get(candidate.decision_id)
                merged[candidate.decision_id] = (
                    _merge(existing, candidate)
                    if existing is not None
                    else candidate.model_copy()
                )
                scores[candidate.decision_id] = (
                    scores.get(candidate.decision_id, 0.0) + contribution
                )
                per_source = contributions.setdefault(candidate.decision_id, {})
                per_source[source] = per_source.get(source, 0.0) + contribution

        fused = [
            FusedCandidate(
                candidate=merged[decision_id],
                score=round(scores[decision_id], 12),
                contributions=contributions[decision_id],
            )
            for decision_id in merged
        ]
        fused.sort(key=lambda item: (-item.score, item.candidate.decision_id))
        return fused


def _merge(
    existing: DecisionCandidate, incoming: DecisionCandidate
) -> DecisionCandidate:
    status = (
        incoming.status
        if _STATUS_PRIORITY[incoming.status] > _STATUS_PRIORITY[existing.status]
        else existing.status
    )
    sources = dict(existing.sources)
    sources.update(incoming.sources)
    return existing.model_copy(
        update={
            "claim": existing.claim or incoming.claim,
            "rationale": existing.rationale or incoming.rationale,
            "status": status,
            "confidence": _stronger_confidence(existing, incoming),
            "target_ids": _union(existing.target_ids, incoming.target_ids),
            "evidence_ids": _union(existing.evidence_ids, incoming.evidence_ids),
            "superseded_by": existing.superseded_by or incoming.superseded_by,
            "sources": sources,
        }
    )


def _union(left: Sequence[str], right: Sequence[str]) -> list[str]:
    return list(dict.fromkeys([*left, *right]))


def _stronger_confidence(
    left: DecisionCandidate, right: DecisionCandidate
) -> AnswerConfidence:
    order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    return (
        right.confidence
        if order[right.confidence.value] > order[left.confidence.value]
        else left.confidence
    )


__all__ = ["Fusion"]
