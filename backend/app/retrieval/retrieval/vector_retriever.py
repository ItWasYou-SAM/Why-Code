"""Semantic candidate retrieval.

The embedding is produced once per question. A malformed or non-finite
embedding is a hard failure: silently searching with a zero vector would
turn a bug into a plausible-looking but meaningless answer.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from app.core.errors import InvalidRecordError
from app.retrieval.models import DecisionCandidate, RetrievalSource
from app.retrieval.ports import EmbeddingProvider, GraphBackend
from app.retrieval.retrieval._support import tag_source


class VectorRetriever:
    def __init__(
        self,
        backend: GraphBackend,
        embedder: EmbeddingProvider,
        *,
        limit: int = 20,
    ) -> None:
        self.backend = backend
        self.embedder = embedder
        self.limit = limit

    def retrieve(self, *, repo: str, text: str) -> list[DecisionCandidate]:
        cleaned = " ".join(text.split()).strip()
        if not cleaned:
            return []
        embedding = self.embedder.embed(cleaned)
        _validate(embedding)
        raw = self.backend.vector_search(
            repo=repo, embedding=embedding, limit=self.limit
        )
        return tag_source(raw, RetrievalSource.VECTOR, limit=self.limit)


def _validate(embedding: Sequence[float]) -> None:
    if not embedding:
        raise InvalidRecordError(
            "embedding provider returned an empty vector", component="retrieval"
        )
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in embedding):
        raise InvalidRecordError(
            "embedding provider returned a non-finite vector", component="retrieval"
        )


__all__ = ["VectorRetriever"]
