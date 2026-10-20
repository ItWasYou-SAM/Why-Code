"""Canonical graph records.

Extraction and mapping produce these records; writers consume them. Nothing
here talks to Neo4j or builds Cypher, so no extractor can accidentally write
to the database. A node's ``key`` is its stable graph identity — the existing
``record_id`` convention — and a relationship names its endpoints by key.

The model is deliberately transport-neutral: ``properties`` is plain data,
labels/relationship types are validated identifiers, and duplicate keys or
dangling endpoints are refused before a writer sees the batch (D-0011).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.errors import InvalidRecordError

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: str, *, field: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{field} must be a Cypher identifier, got {value!r}")
    return value


def _non_empty(value: str, *, field: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value


class GraphNode(BaseModel):
    """One node, ready to be merged by any graph writer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    label: str
    key: str
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: str) -> str:
        return _identifier(value, field="label")

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        return _non_empty(value, field="key")

    # Compatibility aliases for the Component 3 vocabulary this replaces.
    @property
    def node_id(self) -> str:
        return self.key

    @property
    def props(self) -> dict[str, Any]:
        return self.properties


class GraphRelationship(BaseModel):
    """One relationship, with both endpoints named by node key."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    rel_type: str
    start_key: str
    end_key: str
    properties: dict[str, Any] = Field(default_factory=dict)

    @field_validator("rel_type")
    @classmethod
    def _validate_rel_type(cls, value: str) -> str:
        return _identifier(value, field="rel_type")

    @field_validator("start_key", "end_key")
    @classmethod
    def _validate_endpoint(cls, value: str) -> str:
        return _non_empty(value, field="endpoint")

    # Compatibility aliases for the Component 3 vocabulary this replaces.
    @property
    def from_id(self) -> str:
        return self.start_key

    @property
    def to_id(self) -> str:
        return self.end_key

    @property
    def props(self) -> dict[str, Any]:
        return self.properties


class GraphRecords(BaseModel):
    """A complete, self-contained batch of nodes and relationships."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[GraphNode] = Field(default_factory=list)
    relationships: list[GraphRelationship] = Field(default_factory=list)

    def node_keys(self) -> set[str]:
        return {node.key for node in self.nodes}

    def counts(self) -> dict[str, dict[str, int]]:
        nodes: dict[str, int] = {}
        rels: dict[str, int] = {}
        for node in self.nodes:
            nodes[node.label] = nodes.get(node.label, 0) + 1
        for rel in self.relationships:
            rels[rel.rel_type] = rels.get(rel.rel_type, 0) + 1
        return {"nodes": nodes, "rels": rels}

    def assert_integrity(self) -> None:
        known: set[str] = set()
        for node in self.nodes:
            if node.key in known:
                raise InvalidRecordError(
                    f"duplicate graph node key {node.key}",
                    component="graph",
                    record_id=node.key,
                )
            known.add(node.key)
        for rel in self.relationships:
            for endpoint in (rel.start_key, rel.end_key):
                if endpoint not in known:
                    raise InvalidRecordError(
                        f"{rel.rel_type} references missing graph node {endpoint}",
                        component="graph",
                        record_id=endpoint,
                    )


def records_from_jsonl(lines: Iterable[str]) -> GraphRecords:
    """Load a node/relationship JSONL stream back into a record batch.

    A line is a node when it carries ``label`` and a relationship when it
    carries ``rel_type``. Blank lines are ignored; anything else is refused,
    so a truncated or foreign file can never be silently half-loaded.
    """
    nodes: list[GraphNode] = []
    relationships: list[GraphRelationship] = []
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise InvalidRecordError(
                f"invalid JSON on line {number}: {error.msg}", component="graph"
            ) from error
        if not isinstance(payload, dict):
            raise InvalidRecordError(
                f"line {number} is not a JSON object", component="graph"
            )
        if "rel_type" in payload:
            relationships.append(GraphRelationship.model_validate(payload))
        elif "label" in payload:
            nodes.append(GraphNode.model_validate(payload))
        else:
            raise InvalidRecordError(
                f"line {number} is neither a node nor a relationship",
                component="graph",
            )
    records = GraphRecords(nodes=nodes, relationships=relationships)
    records.assert_integrity()
    return records


def records_to_jsonl(records: GraphRecords) -> str:
    """Serialize a record batch as JSONL, nodes first."""
    lines = [
        json.dumps(node.model_dump(), sort_keys=True) for node in records.nodes
    ]
    lines.extend(
        json.dumps(rel.model_dump(), sort_keys=True)
        for rel in records.relationships
    )
    return "\n".join(lines) + ("\n" if lines else "")


__all__ = [
    "GraphNode",
    "GraphRecords",
    "GraphRelationship",
    "records_from_jsonl",
    "records_to_jsonl",
]
