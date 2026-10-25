"""Ports the retrieval pipeline depends on.

Neo4j and the LLM sit behind these protocols. The tests use in-memory fakes;
production adapters can be swapped without changing a single pipeline stage.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from app.retrieval.context.evidence_packer import EvidencePack
from app.retrieval.models import (
    AnswerDraft,
    DecisionCandidate,
    EvidenceItem,
    GraphContext,
    QuestionUnderstanding,
)


class GraphBackend(Protocol):
    def exact_search(
        self, *, repo: str, identifiers: Sequence[str], limit: int
    ) -> Sequence[DecisionCandidate]: ...

    def fulltext_search(
        self, *, repo: str, query: str, limit: int
    ) -> Sequence[DecisionCandidate]: ...

    def vector_search(
        self, *, repo: str, embedding: Sequence[float], limit: int
    ) -> Sequence[DecisionCandidate]: ...

    def expand(self, *, repo: str, decision_id: str) -> GraphContext | None: ...


class LookupBackend(Protocol):
    def get_decision(self, *, repo: str, decision_id: str) -> GraphContext | None: ...

    def get_evidence(self, *, repo: str, evidence_id: str) -> EvidenceItem | None: ...

    def entity_history(
        self, *, repo: str, entity_id: str
    ) -> Sequence[GraphContext]: ...


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]: ...


class TextGenerator(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str: ...


class AnswerSynthesizerLike(Protocol):
    """The synthesis boundary the service depends on.

    Production uses the constrained LLM synthesizer. The offline demo can
    supply a deterministic implementation without changing the pipeline or
    bypassing citation validation.
    """

    def synthesize(
        self,
        *,
        pack: EvidencePack,
        understanding: QuestionUnderstanding,
        strict: bool = False,
    ) -> AnswerDraft: ...


class IndexExecutor(Protocol):
    def run(self, query: str, **parameters: Any) -> Any: ...


__all__ = [
    "AnswerSynthesizerLike",
    "EmbeddingProvider",
    "GraphBackend",
    "IndexExecutor",
    "LookupBackend",
    "TextGenerator",
]
