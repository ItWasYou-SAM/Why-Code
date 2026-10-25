"""Neo4j writer for intent records.

The only place the intent layer performs I/O. A dry run performs zero
database operations; a real write validates the whole batch and, when asked,
verifies that every cited source record exists in the database before the
first statement.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.core.errors import GraphWriteError
from app.graph.batch_writer import DEFAULT_BATCH_SIZE, write_records
from app.intent.graph.cypher_templates import (
    INTENT_LABELS,
    delete_statement,
    schema_statements,
)
from app.intent.graph.intent_records import IntentGraph
from app.intent.validation.supersession import StatusUpdate
from app.retrieval.config import RetrievalConfig
from app.retrieval.indexes.embeddings import (
    HashingEmbeddingProvider,
    embed_graph_records,
)
from app.retrieval.indexes.index_manager import IndexManager
from app.retrieval.ports import EmbeddingProvider


class SessionLike(Protocol):
    def run(self, query: str, **parameters: Any) -> Any: ...


@dataclass
class WriteReport:
    nodes: dict[str, int] = field(default_factory=dict)
    rels: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False
    verified: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "nodes": dict(self.nodes),
            "rels": dict(self.rels),
            "dry_run": self.dry_run,
            "verified": self.verified,
        }


class IntentGraphWriter:
    def __init__(
        self,
        session: SessionLike,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        index_config: RetrievalConfig | None = None,
        embedder: EmbeddingProvider | None = None,
    ) -> None:
        self.session = session
        self.batch_size = batch_size
        self.index_config = index_config or RetrievalConfig()
        self.embedder = embedder or HashingEmbeddingProvider(
            self.index_config.embedding_dimensions
        )

    def prepare_schema(self) -> None:
        for statement in schema_statements(INTENT_LABELS):
            self.session.run(statement)
        # The same idempotent retrieval-index sweep as Component 3. Intent
        # setup may run first, so it creates the structural indexes too.
        IndexManager(self.index_config).ensure(self.session)

    def write(
        self,
        graph: IntentGraph,
        *,
        repo: str,
        dry_run: bool = False,
        verify_evidence: bool = False,
    ) -> WriteReport:
        graph.assert_integrity()
        counts = graph.counts()
        report = WriteReport(
            nodes=counts["nodes"], rels=counts["rels"], dry_run=dry_run
        )
        if dry_run:
            return report
        if verify_evidence:
            source_ids = sorted(
                {ref.source_record_id for ref in graph.evidence_refs}
            )
            missing = self.missing_record_ids(source_ids)
            if missing:
                raise GraphWriteError(
                    "cannot write decisions: evidence missing from Neo4j: "
                    + ", ".join(missing[:5]),
                    component="intent",
                )
            report.verified = True
        try:
            records = embed_graph_records(
                graph.to_records(),
                embedder=self.embedder,
                dimensions=self.index_config.embedding_dimensions,
                max_text_chars=self.index_config.max_embedding_chars,
            )
            write_records(self.session, records, batch_size=self.batch_size)
        except Exception as error:  # noqa: BLE001 - re-raised with taxonomy
            if isinstance(error, GraphWriteError):
                raise
            raise GraphWriteError(
                f"intent write failed: {error}", component="intent"
            ) from error
        return report

    def rebuild(
        self, graph: IntentGraph, *, repo: str, dry_run: bool = False
    ) -> WriteReport:
        graph.assert_integrity()
        if dry_run:
            counts = graph.counts()
            return WriteReport(nodes=counts["nodes"], rels=counts["rels"], dry_run=True)
        self.prepare_schema()
        for label in INTENT_LABELS:
            self.session.run(delete_statement(label), repo=repo)
        return self.write(graph, repo=repo)

    def missing_record_ids(self, record_ids: Sequence[str]) -> list[str]:
        """Return the ids that no node in Neo4j carries (empty means all present)."""
        ids = sorted(set(record_ids))
        if not ids:
            return []
        result = self.session.run(
            "UNWIND $ids AS id MATCH (n {record_id: id}) "
            "RETURN collect(DISTINCT id) AS found",
            ids=ids,
        )
        found = _found_ids(result)
        return [record_id for record_id in ids if record_id not in found]

    def apply_status_updates(self, updates: Sequence[StatusUpdate]) -> int:
        count = 0
        for update in updates:
            self.session.run(
                "MATCH (d:Decision {record_id: $record_id}) "
                "SET d.status = $status, d.superseded_by = $superseded_by, "
                "d.contested_by = $contested_by",
                record_id=update.intent_id,
                status=update.status.value,
                superseded_by=update.superseded_by,
                contested_by=update.contested_by,
            )
            count += 1
        return count


def _found_ids(result: Any) -> set[str]:
    if result is None:
        return set()
    if hasattr(result, "single"):
        record = result.single()
    elif isinstance(result, list):
        record = result[0] if result else None
    else:
        record = result
    if record is None:
        return set()
    try:
        found = record["found"]
    except (TypeError, KeyError, IndexError):
        found = getattr(record, "found", None)
    if isinstance(found, (list, tuple, set)):
        return {str(item) for item in found}
    return set()


__all__ = ["IntentGraphWriter", "WriteReport"]
