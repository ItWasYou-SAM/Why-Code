"""Build the typed query the retriever phases consume.

This module does not execute anything and does not build Cypher. It converts
question understanding into four independent signals — exact identifiers,
full-text terms, vector text and graph seeds — so rank fusion can treat them
separately instead of comparing incompatible scores.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.models import RetrievalQuery as PipelineRetrievalQuery
from app.retrieval.question.models import (
    ENTITY_TYPE_ORDER,
    EntityType,
    QuestionType,
    QuestionUnderstanding,
    TimeConstraint,
)
from app.retrieval.question.normalization import (
    content_tokens,
    lucene_escape,
)


class ExactIdentifier(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    type: EntityType
    identifier: str = Field(min_length=1)
    graph_key: str = Field(min_length=1)


class RetrievalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str | None = None
    question: str = Field(min_length=1)
    question_type: QuestionType
    exact_identifiers: list[ExactIdentifier] = Field(default_factory=list)
    graph_seeds: list[str] = Field(default_factory=list)
    fulltext_terms: list[str] = Field(default_factory=list)
    fulltext_query: str = ""
    vector_text: str = Field(min_length=1)
    time_constraint: TimeConstraint | str | None = None
    requires_supersession_check: bool

    def as_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class QueryBuilder:
    """Adapt the rich Phase 1 plan to the pipeline's compact query record."""

    def __init__(self, *, max_terms: int = 12) -> None:
        self.max_terms = max_terms

    def build(self, understanding: QuestionUnderstanding) -> PipelineRetrievalQuery:
        plan = build_retrieval_query(
            understanding, max_terms=self.max_terms
        )
        return PipelineRetrievalQuery(
            exact_identifiers=[
                item.identifier for item in plan.exact_identifiers
            ],
            fulltext_query=plan.fulltext_query,
            vector_text=plan.vector_text,
        )


def build_retrieval_query(
    understanding: QuestionUnderstanding,
    *,
    max_terms: int = 12,
) -> RetrievalQuery:
    if max_terms < 1:
        raise ValueError("max_terms must be positive")

    exact_identifiers = _exact_identifiers(understanding)
    terms = _fulltext_terms(understanding, max_terms=max_terms)
    clauses = _fulltext_clauses(exact_identifiers, terms)
    return RetrievalQuery(
        repo=understanding.repo,
        question=understanding.question,
        question_type=understanding.question_type,
        exact_identifiers=exact_identifiers,
        graph_seeds=sorted(
            {
                entity.graph_key or entity.normalized
                for entity in understanding.entities
            }
        ),
        fulltext_terms=terms,
        fulltext_query=" OR ".join(clauses),
        vector_text=understanding.question,
        time_constraint=understanding.time_constraint,
        requires_supersession_check=understanding.requires_supersession_check,
    )


def _exact_identifiers(
    understanding: QuestionUnderstanding,
) -> list[ExactIdentifier]:
    seen: set[str] = set()
    identifiers: list[ExactIdentifier] = []
    for entity in sorted(
        understanding.entities,
        key=lambda item: (
            ENTITY_TYPE_ORDER[item.type],
            item.normalized.casefold(),
            item.graph_key or item.normalized,
        ),
    ):
        graph_key = entity.graph_key or entity.normalized
        if graph_key in seen:
            continue
        seen.add(graph_key)
        identifiers.append(
            ExactIdentifier(
                type=entity.type,
                identifier=entity.normalized,
                graph_key=graph_key,
            )
        )
    return identifiers


def _fulltext_terms(
    understanding: QuestionUnderstanding,
    *,
    max_terms: int,
) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for token in content_tokens(understanding.question):
        if len(terms) >= max_terms:
            return terms
        if token in seen:
            continue
        seen.add(token)
        terms.append(token)
    for entity in understanding.entities:
        for token in content_tokens(entity.normalized):
            if len(terms) >= max_terms:
                return terms
            if token in seen:
                continue
            seen.add(token)
            terms.append(token)
    return terms[:max_terms]


def _fulltext_clauses(
    exact_identifiers: list[ExactIdentifier],
    terms: list[str],
) -> list[str]:
    clauses: list[str] = []
    seen: set[str] = set()
    for exact in exact_identifiers:
        clause = f'"{lucene_escape(exact.identifier)}"'
        if clause not in seen:
            seen.add(clause)
            clauses.append(clause)
    for term in terms:
        clause = lucene_escape(term)
        if clause not in seen:
            seen.add(clause)
            clauses.append(clause)
    return clauses


__all__ = [
    "ExactIdentifier",
    "QueryBuilder",
    "RetrievalQuery",
    "build_retrieval_query",
]
