"""Cypher templates for the structural layer.

Labels and relationship types are the only interpolated identifiers, and
they come from a fixed enum-like vocabulary. Every value is a parameter.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

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

# MERGE templates live in ``app.graph.batch_writer`` so both graph halves
# write the same batched statements. This module owns schema only.

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


def constraint_statement(label: str) -> str:
    return CONSTRAINT.format(name=constraint_name(label), label=label)


def index_statement(label: str) -> str:
    return INDEX.format(name=index_name(label), label=label)


def delete_statement(label: str) -> str:
    return DELETE_BY_REPO.format(label=label)


def schema_statements(labels: Iterable[str] = STRUCTURAL_LABELS) -> Iterator[str]:
    """Constraint + index setup for the structural labels, idempotent."""
    labels = list(labels)
    for label in labels:
        yield constraint_statement(label)
    for label in labels:
        yield index_statement(label)
