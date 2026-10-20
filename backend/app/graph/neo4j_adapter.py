"""Schema statements for the evidence graph.

Value shapes and writes now live in ``app.graph.records`` and
``app.graph.batch_writer``. This module keeps the schema side — constraints,
indexes, scoped deletes — and the ``translate`` entry point that turns a
plan into batched statements. Labels are the only interpolated identifiers
and they come from the fixed plan vocabulary.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from app.graph.batch_writer import (
    DEFAULT_BATCH_SIZE,
    merge_node_batch_statement,
    merge_relationship_batch_statement,
    record_statements,
)
from app.graph.ops import GraphPlan


def _slug(label: str) -> str:
    return label.lower()


def constraint_name(label: str) -> str:
    return f"whycode_{_slug(label)}_record_id_unique"


def index_name(label: str) -> str:
    return f"whycode_{_slug(label)}_repo_idx"


def delete_nodes(label: str) -> str:
    return f"MATCH (n:{label} {{repo: $repo}}) DETACH DELETE n"


def constraint_statement(label: str) -> str:
    return (
        f"CREATE CONSTRAINT {constraint_name(label)} IF NOT EXISTS "
        f"FOR (n:{label}) REQUIRE n.record_id IS UNIQUE"
    )


def index_statement(label: str) -> str:
    return (
        f"CREATE INDEX {index_name(label)} IF NOT EXISTS "
        f"FOR (n:{label}) ON (n.repo)"
    )


def constraint_statements(labels: Iterable[str]) -> Iterator[str]:
    for label in labels:
        yield constraint_statement(label)


def index_statements(labels: Iterable[str]) -> Iterator[str]:
    for label in labels:
        yield index_statement(label)


def schema_statements(labels: Iterable[str]) -> Iterator[str]:
    """Constraint + index setup for a label set, idempotent by ``IF NOT EXISTS``.

    Materialized labels are consumed once so the generator can feed both
    halves without the caller rebuilding them.
    """
    labels = list(labels)
    yield from constraint_statements(labels)
    yield from index_statements(labels)


def translate(
    plan: GraphPlan, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> list[tuple[str, dict[str, Any]]]:
    """Translate a plan into grouped, parameterized batch statements."""
    return record_statements(plan.to_records(), batch_size=batch_size)


__all__ = [
    "constraint_name",
    "constraint_statement",
    "constraint_statements",
    "delete_nodes",
    "index_name",
    "index_statement",
    "index_statements",
    "merge_node_batch_statement",
    "merge_relationship_batch_statement",
    "schema_statements",
    "translate",
]
