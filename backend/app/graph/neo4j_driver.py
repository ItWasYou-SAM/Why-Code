"""Neo4j driver boundary.

The driver is the only object that owns a network connection. It calls the
official ``Driver.execute_query`` API, which runs every statement inside a
managed transaction (so transient failures are retried with the driver's
built-in policy) and always names the target database explicitly, as Neo4j
recommends. Queries and values are parameters; no record value is ever
interpolated into Cypher.

Existing writers depend on the tiny ``SessionLike`` protocol
(``run(query, **params)``). :meth:`Neo4jClient.run` adapts that onto
``execute_query``, so the batched writers stay driver-agnostic and testable
against an in-memory fake.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

from app.core.config import Settings
from app.core.errors import GraphWriteError
from app.graph.neo4j_adapter import schema_statements


@dataclass(frozen=True)
class QueryResult:
    """The part of a Neo4j result the pipeline reads: rows and the summary."""

    records: list[Any]
    summary: Any = None

    def single(self) -> Any | None:
        return self.records[0] if self.records else None


def _collect(result: Any) -> QueryResult:
    """``result_transformer_`` that materializes records and closes the stream."""
    records = list(result)
    summary = result.consume()
    return QueryResult(records=records, summary=summary)


class Neo4jClient:
    """A named-database, managed-transaction view of a driver."""

    def __init__(self, driver: Any, *, database: str) -> None:
        if not database or not database.strip():
            raise ValueError("neo4j database must be named explicitly")
        self._driver = driver
        self.database = database

    @classmethod
    def from_settings(cls, settings: Settings) -> Neo4jClient:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
        )
        return cls(driver, database=settings.neo4j_database)

    @classmethod
    def connect(
        cls, *, uri: str, database: str, user: str, password: str
    ) -> Neo4jClient:
        """Connect to an explicit URI/database with explicit credentials.

        Used by the validation CLI, which needs only Neo4j access and must
        not require the GitHub/LLM secrets that :class:`Settings` demands.
        """
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(user, password))
        return cls(driver, database=database)

    def run(self, query: str, **parameters: Any) -> QueryResult:
        try:
            result = self._driver.execute_query(
                query,
                parameters_=parameters,
                database_=self.database,
                result_transformer_=_collect,
            )
        except Exception as error:  # noqa: BLE001 - re-raised with taxonomy
            if isinstance(error, GraphWriteError):
                raise
            raise GraphWriteError(
                f"neo4j query failed: {error}",
                component="graph",
            ) from error
        return cast(QueryResult, result)

    def ensure_schema(self, labels: Iterable[str]) -> list[str]:
        """Create constraints and indexes for ``labels``. Idempotent.

        ``IF NOT EXISTS`` makes a second run a no-op, so this is the "run
        once during setup" step that is also safe to call on every load.
        """
        statements = list(schema_statements(labels))
        for statement in statements:
            self.run(statement)
        return statements

    def verify_connectivity(self) -> None:
        try:
            self._driver.verify_connectivity()
        except Exception as error:  # noqa: BLE001
            raise GraphWriteError(
                f"neo4j connection failed: {error}", component="graph"
            ) from error

    def close(self) -> None:
        self._driver.close()


__all__ = ["Neo4jClient", "QueryResult"]
