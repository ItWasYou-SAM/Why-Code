"""Neo4j writer for structural records.

The writer is the only place structural code performs I/O. It validates
the whole graph before emitting a single statement, so an invalid
relationship can never leave a half-written enrichment behind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.errors import GraphWriteError
from app.graph.batch_writer import DEFAULT_BATCH_SIZE, record_statements, write_records
from app.structural.graph.cypher_templates import (
    STRUCTURAL_LABELS,
    delete_statement,
    schema_statements,
)
from app.structural.graph.graph_records import StructuralGraph


class SessionLike(Protocol):
    def run(self, query: str, **parameters: Any) -> Any: ...


@dataclass
class WriteReport:
    nodes: dict[str, int] = field(default_factory=dict)
    edges: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"nodes": dict(self.nodes), "edges": dict(self.edges), "dry_run": self.dry_run}


class StructuralGraphWriter:
    def __init__(
        self, session: SessionLike, *, batch_size: int = DEFAULT_BATCH_SIZE
    ) -> None:
        self.session = session
        self.batch_size = batch_size

    def prepare_schema(self) -> None:
        for statement in schema_statements(STRUCTURAL_LABELS):
            self.session.run(statement)

    def write(self, graph: StructuralGraph, *, repo: str, dry_run: bool = False) -> WriteReport:
        graph.assert_integrity()
        counts = graph.counts()
        report = WriteReport(
            nodes=counts["nodes"], edges=counts["edges"], dry_run=dry_run
        )
        if dry_run:
            return report
        try:
            write_records(
                self.session, graph.to_records(), batch_size=self.batch_size
            )
        except Exception as error:  # noqa: BLE001
            if isinstance(error, GraphWriteError):
                raise
            raise GraphWriteError(
                f"structural write failed: {error}", component="structural"
            ) from error
        return report

    def rebuild(self, graph: StructuralGraph, *, repo: str, dry_run: bool = False) -> WriteReport:
        graph.assert_integrity()
        if dry_run:
            counts = graph.counts()
            return WriteReport(nodes=counts["nodes"], edges=counts["edges"], dry_run=True)
        self.prepare_schema()
        for label in STRUCTURAL_LABELS:
            self.session.run(delete_statement(label), repo=repo)
        return self.write(graph, repo=repo)


def translate(graph: StructuralGraph, *, batch_size: int = DEFAULT_BATCH_SIZE) -> Any:
    """Return grouped, parameterized statements for a structural graph."""
    return record_statements(graph.to_records(), batch_size=batch_size)
