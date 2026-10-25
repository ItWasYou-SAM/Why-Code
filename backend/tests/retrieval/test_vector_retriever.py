"""Vector retrieval embeds once and refuses malformed vectors."""

from __future__ import annotations

import math

import pytest
from retrieval_fixtures import REPO, FakeBackend, FakeEmbedder, candidate

from app.core.errors import InvalidRecordError
from app.retrieval.models import RetrievalSource
from app.retrieval.retrieval.vector_retriever import VectorRetriever


def test_question_is_embedded_once_and_passed_to_the_backend() -> None:
    backend = FakeBackend(vector=[candidate()])
    embedder = FakeEmbedder([0.5, 0.25])
    VectorRetriever(backend, embedder, limit=7).retrieve(
        repo=REPO, text="  why are retries disabled  "
    )
    assert embedder.calls == ["why are retries disabled"]
    assert backend.calls == [
        (
            "vector",
            {"repo": REPO, "embedding": [0.5, 0.25], "limit": 7},
        )
    ]


def test_candidates_are_tagged_as_vector_with_rank() -> None:
    backend = FakeBackend(vector=[candidate("dec_001"), candidate("dec_002")])
    rows = VectorRetriever(backend, FakeEmbedder()).retrieve(
        repo=REPO, text="retries"
    )
    assert [row.sources[RetrievalSource.VECTOR] for row in rows] == [1, 2]


def test_blank_text_is_a_no_op() -> None:
    backend = FakeBackend(vector=[candidate()])
    embedder = FakeEmbedder()
    assert VectorRetriever(backend, embedder).retrieve(repo=REPO, text="  ") == []
    assert embedder.calls == []
    assert backend.calls == []


def test_empty_embedding_is_rejected() -> None:
    backend = FakeBackend(vector=[candidate()])
    with pytest.raises(InvalidRecordError, match="empty vector"):
        VectorRetriever(backend, FakeEmbedder([])).retrieve(
            repo=REPO, text="retries"
        )
    assert backend.calls == []


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_embedding_is_rejected(bad: float) -> None:
    backend = FakeBackend(vector=[candidate()])
    with pytest.raises(InvalidRecordError, match="non-finite"):
        VectorRetriever(backend, FakeEmbedder([0.1, bad])).retrieve(
            repo=REPO, text="retries"
        )
    assert backend.calls == []


def test_limit_is_enforced() -> None:
    backend = FakeBackend(
        vector=[candidate(f"dec_{number}") for number in range(5)],
        honor_limit=False,
    )
    rows = VectorRetriever(backend, FakeEmbedder(), limit=2).retrieve(
        repo=REPO, text="retries"
    )
    assert len(rows) == 2
