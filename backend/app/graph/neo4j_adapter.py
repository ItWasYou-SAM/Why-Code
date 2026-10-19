"""Ops -> parameterized Cypher.

Every value travels as a driver parameter; label and relationship type are
the only identifiers interpolated, and they come from the fixed plan
vocabulary. Statement shapes are pinned by tests because identical Cypher
across runs is what makes replays debuggable.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from app.graph.ops import GraphPlan


def _slug(label: str) -> str:
    return label.lower()


def constraint_name(label: str) -> str:
    return f"whycode_{_slug(label)}_record_id_unique"


def index_name(label: str) -> str:
    return f"whycode_{_slug(label)}_repo_idx"


def merge_node_statement(label: str) -> str:
    return f"MERGE (n:{label} {{record_id: $record_id}}) SET n += $props"


def merge_rel_statement(rel_type: str) -> str:
    return (
        f"MERGE (a {{record_id: $from_record_id}}) "
        f"MERGE (b {{record_id: $to_record_id}}) "
        f"MERGE (a)-[r:{rel_type}]->(b) SET r += $props"
    )


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


def translate(plan: GraphPlan) -> Iterator[tuple[str, dict[str, Any]]]:
    for node in plan.nodes:
        yield (
            merge_node_statement(node.label),
            {"record_id": node.record_id, "props": node.props},
        )
    for rel in plan.rels:
        yield (
            merge_rel_statement(rel.rel_type),
            {
                "from_record_id": rel.from_record_id,
                "to_record_id": rel.to_record_id,
                "props": rel.props,
            },
        )
