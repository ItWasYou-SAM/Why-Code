"""Intent-layer schema statements.

Labels and relationship types are a fixed vocabulary; the shared batched
MERGE writer in ``app.graph.batch_writer`` does the writing. This module
owns only constraints, indexes and scoped deletes for the intent labels.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

INTENT_LABELS = (
    "Decision",
    "Constraint",
    "Reversal",
    "Incident",
    "Evidence",
    "Person",
)

INTENT_REL_TYPES = (
    "SUPPORTED_BY",
    "GOVERNS",
    "PRODUCED_IN",
    "MOTIVATED_BY",
    "REVERSES",
    "REVISES",
    "SUPERSEDES",
    "CONFLICTS_WITH",
    "REPRESENTS",
    "AUTHORED",
    "REVIEWED",
    "ADVOCATED",
    "OBJECTED_TO",
)

CONSTRAINT = (
    "CREATE CONSTRAINT {name} IF NOT EXISTS "
    "FOR (n:{label}) REQUIRE n.record_id IS UNIQUE"
)
INDEX = "CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.repo)"
DELETE_BY_REPO = "MATCH (n:{label} {{repo: $repo}}) DETACH DELETE n"


def constraint_name(label: str) -> str:
    return f"whycode_intent_{label.lower()}_record_id_unique"


def index_name(label: str) -> str:
    return f"whycode_intent_{label.lower()}_repo_idx"


def constraint_statement(label: str) -> str:
    return CONSTRAINT.format(name=constraint_name(label), label=label)


def index_statement(label: str) -> str:
    return INDEX.format(name=index_name(label), label=label)


def delete_statement(label: str) -> str:
    return DELETE_BY_REPO.format(label=label)


def schema_statements(
    labels: Iterable[str] = INTENT_LABELS,
) -> Iterator[str]:
    labels = list(labels)
    for label in labels:
        yield constraint_statement(label)
    for label in labels:
        yield index_statement(label)


__all__ = [
    "INTENT_LABELS",
    "INTENT_REL_TYPES",
    "constraint_name",
    "constraint_statement",
    "delete_statement",
    "index_name",
    "index_statement",
    "schema_statements",
]
