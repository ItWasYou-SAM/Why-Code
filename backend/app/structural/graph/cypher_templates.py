"""Cypher templates for the structural layer.

Labels and relationship types are the only interpolated identifiers, and
they come from a fixed enum-like vocabulary. Every value is a parameter.
"""

from __future__ import annotations

STRUCTURAL_LABELS = (
    "File",
    "Symbol",
    "CodeRegion",
    "ConfigKey",
    "ExternalRef",
    "Doc",
)

STRUCTURAL_REL_TYPES = (
    "DEFINES",
    "IMPORTS",
    "REFERENCES",
    "USED_BY",
    "HAS_REGION",
    "DOCUMENTED_BY",
    "DECLARES",
)

MERGE_NODE = "MERGE (n:{label} {{record_id: $record_id}}) SET n += $props"

MERGE_EDGE = (
    "MERGE (a {{record_id: $from_id}}) "
    "MERGE (b {{record_id: $to_id}}) "
    "MERGE (a)-[r:{rel_type}]->(b) SET r += $props"
)

CONSTRAINT = (
    "CREATE CONSTRAINT {name} IF NOT EXISTS "
    "FOR (n:{label}) REQUIRE n.record_id IS UNIQUE"
)

INDEX = "CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) ON (n.repo)"

DELETE_BY_REPO = "MATCH (n:{label} {{repo: $repo}}) DETACH DELETE n"


def constraint_name(label: str) -> str:
    return f"whycode_structural_{label.lower()}_record_id_unique"


def index_name(label: str) -> str:
    return f"whycode_structural_{label.lower()}_repo_idx"


def merge_node_statement(label: str) -> str:
    return MERGE_NODE.format(label=label)


def merge_edge_statement(rel_type: str) -> str:
    return MERGE_EDGE.format(rel_type=rel_type)


def constraint_statement(label: str) -> str:
    return CONSTRAINT.format(name=constraint_name(label), label=label)


def index_statement(label: str) -> str:
    return INDEX.format(name=index_name(label), label=label)


def delete_statement(label: str) -> str:
    return DELETE_BY_REPO.format(label=label)
