"""Exact retrieval always runs for resolved identifiers and records rank,
never a raw score."""

from __future__ import annotations

from retrieval_fixtures import CONFIG_ID, REPO, FakeBackend, candidate

from app.retrieval.models import RetrievalSource
from app.retrieval.retrieval.exact_retriever import ExactRetriever


def test_empty_identifiers_do_not_hit_the_backend() -> None:
    backend = FakeBackend(exact=[candidate()])
    assert ExactRetriever(backend).retrieve(repo=REPO, identifiers=[]) == []
    assert backend.calls == []


def test_blank_identifiers_are_ignored() -> None:
    backend = FakeBackend(exact=[candidate()])
    ExactRetriever(backend).retrieve(repo=REPO, identifiers=["", "   "])
    assert backend.calls == []


def test_backend_receives_repo_identifiers_and_limit() -> None:
    backend = FakeBackend(exact=[candidate()])
    retriever = ExactRetriever(backend, limit=4)
    retriever.retrieve(repo=REPO, identifiers=[CONFIG_ID])
    assert backend.calls == [
        (
            "exact",
            {"repo": REPO, "identifiers": [CONFIG_ID], "limit": 4},
        )
    ]


def test_candidates_are_tagged_with_their_exact_rank() -> None:
    backend = FakeBackend(
        exact=[
            candidate("dec_001"),
            candidate("dec_002"),
            candidate("dec_003"),
        ]
    )
    rows = ExactRetriever(backend).retrieve(repo=REPO, identifiers=[CONFIG_ID])
    assert [row.sources[RetrievalSource.EXACT] for row in rows] == [1, 2, 3]
    assert [row.decision_id for row in rows] == ["dec_001", "dec_002", "dec_003"]


def test_duplicate_decisions_keep_the_first_rank() -> None:
    backend = FakeBackend(
        exact=[candidate("dec_001"), candidate("dec_001"), candidate("dec_002")]
    )
    rows = ExactRetriever(backend).retrieve(repo=REPO, identifiers=[CONFIG_ID])
    assert [row.decision_id for row in rows] == ["dec_001", "dec_002"]
    assert rows[0].sources[RetrievalSource.EXACT] == 1
    assert rows[1].sources[RetrievalSource.EXACT] == 2


def test_limit_is_enforced_even_if_the_backend_over_returns() -> None:
    backend = FakeBackend(
        exact=[candidate(f"dec_{number}") for number in range(10)],
        honor_limit=False,
    )
    rows = ExactRetriever(backend, limit=3).retrieve(
        repo=REPO, identifiers=[CONFIG_ID]
    )
    assert len(rows) == 3


def test_existing_source_ranks_are_preserved() -> None:
    item = candidate(
        sources={RetrievalSource.FULLTEXT: 7}
    )
    backend = FakeBackend(exact=[item])
    rows = ExactRetriever(backend).retrieve(repo=REPO, identifiers=[CONFIG_ID])
    assert rows[0].sources == {
        RetrievalSource.FULLTEXT: 7,
        RetrievalSource.EXACT: 1,
    }
