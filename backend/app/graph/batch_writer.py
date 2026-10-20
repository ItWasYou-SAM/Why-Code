"""Batched graph writes.

The writer is the only place that turns graph records into Cypher. Statements
are grouped by label/relationship type and sent with ``UNWIND``, so a normal
load costs a handful of round trips instead of one per node. Values always
travel as parameters; only the validated label/relationship type is
interpolated (see ``app.graph.records``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from typing import Any, Protocol

from app.graph.records import GraphNode, GraphRecords, GraphRelationship

# The driver-facing writer sends ``UNWIND`` batches in this range: large
# enough to amortize round trips, small enough to stay well under a
# transaction's memory budget.
DEFAULT_BATCH_SIZE = 250
MIN_BATCH_SIZE = 200
MAX_BATCH_SIZE = 500


class SessionLike(Protocol):
    def run(self, query: str, **parameters: Any) -> Any: ...


def merge_node_batch_statement(label: str) -> str:
    return (
        f"UNWIND $nodes AS node "
        f"MERGE (n:{label} {{record_id: node.key}}) "
        f"SET n += node.properties"
    )


def merge_relationship_batch_statement(rel_type: str) -> str:
    return (
        f"UNWIND $relationships AS rel "
        f"MATCH (a {{record_id: rel.start_key}}) "
        f"MATCH (b {{record_id: rel.end_key}}) "
        f"MERGE (a)-[r:{rel_type}]->(b) "
        f"SET r += rel.properties"
    )


def _chunks[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    if size < 1:
        raise ValueError("batch_size must be at least 1")
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _group[T](items: Sequence[T], key: Callable[[T], str]) -> dict[str, list[T]]:
    """Group while preserving first-seen order, so replays are deterministic."""
    grouped: dict[str, list[T]] = {}
    for item in items:
        grouped.setdefault(key(item), []).append(item)
    return grouped


def node_batch_parameters(nodes: Sequence[GraphNode]) -> list[dict[str, Any]]:
    return [
        {"key": node.key, "properties": node.properties} for node in nodes
    ]


def relationship_batch_parameters(
    relationships: Sequence[GraphRelationship],
) -> list[dict[str, Any]]:
    return [
        {
            "start_key": rel.start_key,
            "end_key": rel.end_key,
            "properties": rel.properties,
        }
        for rel in relationships
    ]


def record_statements(
    records: GraphRecords, *, batch_size: int = DEFAULT_BATCH_SIZE
) -> list[tuple[str, dict[str, Any]]]:
    """Translate one record batch into grouped, parameterized statements.

    Nodes are emitted before relationships: the relationship statement uses
    ``MATCH`` for endpoints, and those endpoints must exist in the same
    transaction. ``batch_size`` defaults to :data:`DEFAULT_BATCH_SIZE`; the
    driver-facing writer keeps it in the ``MIN_BATCH_SIZE``–``MAX_BATCH_SIZE``
    range.
    """
    records.assert_integrity()

    statements: list[tuple[str, dict[str, Any]]] = []
    for label, nodes in _group(records.nodes, lambda node: node.label).items():
        for chunk in _chunks(nodes, batch_size):
            statements.append(
                (
                    merge_node_batch_statement(label),
                    {"nodes": node_batch_parameters(chunk)},
                )
            )
    for rel_type, rels in _group(
        records.relationships, lambda rel: rel.rel_type
    ).items():
        for rel_chunk in _chunks(rels, batch_size):
            statements.append(
                (
                    merge_relationship_batch_statement(rel_type),
                    {"relationships": relationship_batch_parameters(rel_chunk)},
                )
            )
    return statements


def write_records(
    session: SessionLike,
    records: GraphRecords,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> int:
    """Execute a record batch and return the number of statements sent."""
    statements = record_statements(records, batch_size=batch_size)
    for query, parameters in statements:
        session.run(query, **parameters)
    return len(statements)


__all__ = [
    "DEFAULT_BATCH_SIZE",
    "MAX_BATCH_SIZE",
    "MIN_BATCH_SIZE",
    "merge_node_batch_statement",
    "merge_relationship_batch_statement",
    "node_batch_parameters",
    "record_statements",
    "relationship_batch_parameters",
    "write_records",
]
