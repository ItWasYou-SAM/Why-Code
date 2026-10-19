"""Graph test doubles.

FakeGraphSession records every Cypher execution and can reject writes,
so the suite can prove contracts like "dry-run performs zero database
operations" and "integrity failures happen before the first write".
FakeOpsSink just collects planned ops one by one. The skip-gate keeps
real-AuraDB integration tests opt-in via environment variables rather
than silently skipped or accidentally slow."""

from __future__ import annotations

import os
from typing import Any


class FakeGraphSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.writes_blocked = False

    def run(self, query: str, **parameters: Any) -> None:
        if self.writes_blocked:
            raise AssertionError(
                f"session wrote during a no-write context: {query!r}"
            )
        self.calls.append((query, parameters))

    @property
    def executed(self) -> int:
        return len(self.calls)

    @property
    def parameters_seen(self) -> list[dict[str, Any]]:
        return [params for _, params in self.calls]


class FakeOpsSink:
    """Collects ops as the adapter receives them; useful to check the
    builder never skips a node and never writes twice."""

    def __init__(self) -> None:
        self.nodes: list[tuple[str, str, dict[str, Any]]] = []
        self.rels: list[tuple[str, str, str]] = []

    def merge_node(self, label: str, record_id: str, props: dict[str, Any]) -> None:
        self.nodes.append((label, record_id, props))

    def merge_rel(self, rel_type: str, from_record_id: str, to_record_id: str) -> None:
        self.rels.append((rel_type, from_record_id, to_record_id))


def clear_writes(sink: FakeOpsSink) -> None:
    sink.nodes.clear()
    sink.rels.clear()


def real_neo4j_env() -> dict[str, str]:
    """Env contract for integration tests; empty dict means skipped."""
    uri = os.environ.get("WHYCODE_TEST_NEO4J_URI", "")
    if not uri:
        return {}
    return {
        "uri": uri,
        "user": os.environ.get("WHYCODE_TEST_NEO4J_USER", "neo4j"),
        "password": os.environ.get("WHYCODE_TEST_NEO4J_PASSWORD", ""),
        "database": os.environ.get("WHYCODE_TEST_NEO4J_DATABASE", "neo4j"),
    }
