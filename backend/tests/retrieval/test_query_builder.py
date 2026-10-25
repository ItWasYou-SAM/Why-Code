"""Retrieval query contracts."""

from __future__ import annotations

from fixtures.graph_fixtures import (
    CONFIG_RETRIES_KEY,
    FILE_CLIENT_KEY,
    REPO,
    static_catalog,
)

from app.retrieval.config import RetrievalConfig
from app.retrieval.question.entity_extractor import EntityExtractor
from app.retrieval.question.models import TimeConstraint, TimeConstraintKind
from app.retrieval.question.normalization import lucene_escape
from app.retrieval.question.query_builder import build_retrieval_query


def understanding(question: str):
    extractor = EntityExtractor(
        static_catalog(),
        RetrievalConfig(repo=REPO, enable_llm_entity_extraction=False),
    )
    return extractor.extract(question)


def test_query_carries_every_signal_separately() -> None:
    query = build_retrieval_query(
        understanding("Why are retries disabled by default in httpx?")
    )
    assert query.repo == REPO
    assert any(
        item.graph_key == CONFIG_RETRIES_KEY
        for item in query.exact_identifiers
    )
    assert CONFIG_RETRIES_KEY in query.graph_seeds
    assert "retries" in query.fulltext_terms
    assert '"retries"' in query.fulltext_query
    assert query.vector_text == query.question
    assert query.requires_supersession_check


def test_query_is_stable_across_runs() -> None:
    first = build_retrieval_query(
        understanding("Why are retries disabled by default in httpx?")
    )
    second = build_retrieval_query(
        understanding("Why are retries disabled by default in httpx?")
    )
    assert first == second


def test_file_path_is_escaped_in_the_fulltext_query() -> None:
    query = build_retrieval_query(
        understanding("Why does httpx/_client.py exist?")
    )
    assert any(
        item.graph_key == FILE_CLIENT_KEY
        for item in query.exact_identifiers
    )
    escaped = lucene_escape("httpx/_client.py")
    assert escaped == "httpx\\/_client.py"
    assert f'"{escaped}"' in query.fulltext_query


def test_lucene_special_characters_are_escaped() -> None:
    assert lucene_escape("Client.send+retry") == "Client.send\\+retry"
    assert lucene_escape("a&&b") == "a\\&\\&b"
    assert lucene_escape("why?") == "why\\?"


def test_time_constraint_passes_through() -> None:
    query: object = build_retrieval_query(
        understanding("Why were retries limited before 2024?")
    )
    constraint = query.time_constraint
    assert isinstance(constraint, TimeConstraint)
    assert constraint.kind is TimeConstraintKind.BEFORE


def test_max_terms_is_enforced() -> None:
    query = build_retrieval_query(
        understanding("Why are retries disabled by default in httpx?"),
        max_terms=2,
    )
    assert len(query.fulltext_terms) <= 2


def test_as_dict_exposes_the_question_type() -> None:
    query = build_retrieval_query(
        understanding("Why are retries disabled by default in httpx?")
    )
    payload = query.as_dict()
    assert payload["question_type"] == "CONFIG_WHY"
    assert isinstance(payload["exact_identifiers"], list)
