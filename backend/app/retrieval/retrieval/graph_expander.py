"""Graph expansion around already-retrieved decisions.

This is the step that makes the pipeline GraphRAG: every candidate is
expanded through ``SUPPORTED_BY``, ``GOVERNS``, ``PRODUCED_IN`` and
supersession edges before evidence is packed.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.retrieval.models import DecisionCandidate, GraphContext
from app.retrieval.ports import GraphBackend


class GraphExpander:
    def __init__(self, backend: GraphBackend, *, limit: int = 12) -> None:
        self.backend = backend
        self.limit = limit

    def expand(
        self, *, repo: str, candidates: Sequence[DecisionCandidate]
    ) -> list[GraphContext]:
        contexts: list[GraphContext] = []
        seen: set[str] = set()
        for candidate in candidates:
            if len(contexts) >= self.limit:
                break
            if candidate.decision_id in seen:
                continue
            seen.add(candidate.decision_id)
            context = self.backend.expand(repo=repo, decision_id=candidate.decision_id)
            if context is None:
                continue
            if context.decision.decision_id != candidate.decision_id:
                continue
            if not context.decision.claim:
                context = context.model_copy(
                    update={
                        "decision": context.decision.model_copy(
                            update={
                                "claim": candidate.claim,
                                "rationale": candidate.rationale,
                                "target_ids": candidate.target_ids
                                or context.decision.target_ids,
                                "status": candidate.status,
                            }
                        )
                    }
                )
            contexts.append(context)
        return contexts


__all__ = ["GraphExpander"]
