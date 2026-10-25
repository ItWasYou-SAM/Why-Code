"""Offline demo composition root.

The demo uses the real retrieval pipeline: question understanding, exact /
full-text / vector retrieval, graph expansion, WRRF fusion, reranking,
evidence packing, synthesis and citation validation. Only the graph and the
model are replaced with deterministic fixtures, so the judge-facing flow does
not depend on a live Neo4j instance or an API key.
"""

from __future__ import annotations

from typing import Any

from app.retrieval.config import RetrievalConfig
from app.retrieval.demo.backend import DemoBackend
from app.retrieval.demo.data import DEMO_REPO, build_contexts, build_entity_index
from app.retrieval.demo.embeddings import DemoEmbeddingProvider
from app.retrieval.demo.synthesizer import DemoSynthesizer
from app.retrieval.service import RetrievalService


class _UnusedGenerator:
    """Satisfy the TextGenerator port; the demo never calls a model."""

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        raise RuntimeError("the deterministic demo does not call a text model")


def create_demo_service(
    config: RetrievalConfig | None = None,
) -> RetrievalService:
    resolved = config or RetrievalConfig(
        repo=DEMO_REPO,
        enable_llm_entity_extraction=False,
    )
    if not resolved.repo:
        resolved = resolved.model_copy(update={"repo": DEMO_REPO})
    embedder = DemoEmbeddingProvider()
    backend = DemoBackend(contexts=build_contexts(), embedder=embedder)
    return RetrievalService(
        backend=backend,
        embedder=embedder,
        generator=_UnusedGenerator(),
        config=resolved,
        index=build_entity_index(),
        lookup=backend,
        synthesizer_factory=DemoSynthesizer,
    )


__all__ = [
    "DemoBackend",
    "DemoEmbeddingProvider",
    "DemoSynthesizer",
    "create_demo_service",
]
