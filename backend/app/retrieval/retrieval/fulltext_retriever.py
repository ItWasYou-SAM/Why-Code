"""Full-text (BM25-ranked) candidate retrieval."""

from __future__ import annotations

from app.retrieval.models import DecisionCandidate, RetrievalSource
from app.retrieval.ports import GraphBackend
from app.retrieval.retrieval._support import tag_source


class FullTextRetriever:
    def __init__(self, backend: GraphBackend, *, limit: int = 20) -> None:
        self.backend = backend
        self.limit = limit

    def retrieve(self, *, repo: str, query: str) -> list[DecisionCandidate]:
        cleaned = " ".join(query.split()).strip()
        if not cleaned:
            return []
        raw = self.backend.fulltext_search(repo=repo, query=cleaned, limit=self.limit)
        return tag_source(raw, RetrievalSource.FULLTEXT, limit=self.limit)


__all__ = ["FullTextRetriever"]
