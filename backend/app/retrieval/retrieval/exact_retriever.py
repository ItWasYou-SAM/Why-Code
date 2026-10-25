"""Exact-identifier retrieval.

This runs first and never asks a model to guess an identifier. A backend
adapter is responsible for the Cypher lookup; this stage enforces the limit,
deduplicates, and records the exact rank.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.retrieval.models import DecisionCandidate, RetrievalSource
from app.retrieval.ports import GraphBackend
from app.retrieval.retrieval._support import tag_source


class ExactRetriever:
    def __init__(self, backend: GraphBackend, *, limit: int = 10) -> None:
        self.backend = backend
        self.limit = limit

    def retrieve(
        self, *, repo: str, identifiers: Sequence[str]
    ) -> list[DecisionCandidate]:
        ordered = [identifier for identifier in identifiers if identifier.strip()]
        if not ordered:
            return []
        raw = self.backend.exact_search(
            repo=repo, identifiers=ordered, limit=self.limit
        )
        return tag_source(raw, RetrievalSource.EXACT, limit=self.limit)


__all__ = ["ExactRetriever"]
