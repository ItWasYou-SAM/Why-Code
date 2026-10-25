"""Full-text retrieval is lexical: queries are passed through, ranks are
recorded, and an empty query is a no-op."""

from __future__ import annotations

from retrieval_fixtures import REPO, FakeBackend, candidate

from app.retrieval.models import RetrievalSource
from app.retrieval.retrieval.fulltext_retriever import FullTextRetriever


def test_empty_query_does_not_hit_the_backend() -> None:
    backend = FakeBackend(fulltext=[candidate()])
    assert FullTextRetriever(backend).retrieve(repo=REPO, query="   ") == []
    assert backend.calls == []


def test_repo_query_and_limit_are_forwarded() -> None:
    backend = FakeBackend(fulltext=[candidate()])
    FullTextRetriever(backend, limit=9).retrieve(
        repo=REPO, query="  retries disabled  "
    )
    assert backend.calls == [
        (
            "fulltext",
            {"repo": REPO, "query": "retries disabled", "limit": 9},
        )
    ]


def test_candidates_are_tagged_as_fulltext_with_rank() -> None:
    backend = FakeBackend(
        fulltext=[candidate("dec_001"), candidate("dec_002")]
    )
    rows = FullTextRetriever(backend).retrieve(repo=REPO, query="retries")
    assert [row.sources[RetrievalSource.FULLTEXT] for row in rows] == [1, 2]


def test_duplicates_are_collapsed() -> None:
    backend = FakeBackend(
        fulltext=[candidate("dec_001"), candidate("dec_001")]
    )
    rows = FullTextRetriever(backend).retrieve(repo=REPO, query="retries")
    assert [row.decision_id for row in rows] == ["dec_001"]


def test_limit_is_enforced() -> None:
    backend = FakeBackend(
        fulltext=[candidate(f"dec_{number}") for number in range(6)],
        honor_limit=False,
    )
    rows = FullTextRetriever(backend, limit=2).retrieve(repo=REPO, query="retry")
    assert len(rows) == 2
