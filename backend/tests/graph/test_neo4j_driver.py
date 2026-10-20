"""Neo4j driver boundary contracts.

The client is the only place a real connection is opened. These tests pin
the driver-level guarantees Phase 9 relies on: every query goes through
``execute_query`` (managed transaction), every call names its database
explicitly, values travel as parameters, and schema setup is an idempotent
``IF NOT EXISTS`` sweep.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.core.errors import GraphWriteError
from app.graph.batch_writer import (
    DEFAULT_BATCH_SIZE,
    MAX_BATCH_SIZE,
    MIN_BATCH_SIZE,
)
from app.graph.neo4j_driver import Neo4jClient, QueryResult


class FakeResult:
    """The slice of ``neo4j.Result`` the client consumes."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def __iter__(self):
        return iter(self._records)

    def consume(self) -> dict[str, Any]:
        return {"records": len(self._records)}


class FakeDriver:
    def __init__(self, records: list[dict[str, Any]] | None = None) -> None:
        self.records = records or []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.closed = False
        self.verify_calls = 0
        self.fail = False

    def execute_query(self, query: str, /, **kwargs: Any) -> Any:
        if self.fail:
            raise RuntimeError("transient failure")
        self.calls.append((query, kwargs))
        result = FakeResult(self.records)
        transformer = kwargs.get("result_transformer_")
        return transformer(result) if transformer else result

    def verify_connectivity(self, **kwargs: Any) -> None:
        self.verify_calls += 1

    def close(self) -> None:
        self.closed = True


def make_client(records: list[dict[str, Any]] | None = None) -> tuple[Neo4jClient, FakeDriver]:
    driver = FakeDriver(records)
    return Neo4jClient(driver, database="neo4j-custom"), driver


class TestRun:
    def test_uses_execute_query_with_an_explicit_database(self) -> None:
        client, driver = make_client()
        client.run("MATCH (n) RETURN count(n) AS c", repo="encode/httpx")
        query, kwargs = driver.calls[0]
        assert query == "MATCH (n) RETURN count(n) AS c"
        assert kwargs["database_"] == "neo4j-custom"
        assert kwargs["parameters_"] == {"repo": "encode/httpx"}
        assert callable(kwargs["result_transformer_"])

    def test_values_are_parameters_not_interpolation(self) -> None:
        client, driver = make_client()
        client.run("MATCH (f:File {path: $path}) RETURN f", path="a/b.py")
        query, kwargs = driver.calls[0]
        assert "a/b.py" not in query
        assert kwargs["parameters_"] == {"path": "a/b.py"}

    def test_single_returns_first_record(self) -> None:
        client, _ = make_client([{"c": 7}, {"c": 8}])
        result = client.run("RETURN 1")
        assert isinstance(result, QueryResult)
        assert result.single() == {"c": 7}

    def test_single_returns_none_when_empty(self) -> None:
        client, _ = make_client([])
        assert client.run("RETURN 1").single() is None

    def test_driver_failure_is_re_wrapped(self) -> None:
        client, driver = make_client()
        driver.fail = True
        with pytest.raises(GraphWriteError):
            client.run("RETURN 1")

    def test_database_must_be_named(self) -> None:
        with pytest.raises(ValueError):
            Neo4jClient(FakeDriver(), database="  ")

    def test_verify_connectivity_wraps_failure(self) -> None:
        client, driver = make_client()
        client.verify_connectivity()
        assert driver.verify_calls == 1

        class Boom(FakeDriver):
            def verify_connectivity(self, **kwargs: Any) -> None:
                raise RuntimeError("unreachable")

        with pytest.raises(GraphWriteError):
            Neo4jClient(Boom(), database="neo4j").verify_connectivity()

    def test_close_delegates_to_driver(self) -> None:
        client, driver = make_client()
        client.close()
        assert driver.closed


class TestSchema:
    def test_ensure_schema_is_idempotent_constraints_then_indexes(self) -> None:
        client, driver = make_client()
        statements = client.ensure_schema(["File", "Symbol"])
        assert len(statements) == 4
        assert statements[:2] == [
            "CREATE CONSTRAINT whycode_file_record_id_unique IF NOT EXISTS "
            "FOR (n:File) REQUIRE n.record_id IS UNIQUE",
            "CREATE CONSTRAINT whycode_symbol_record_id_unique IF NOT EXISTS "
            "FOR (n:Symbol) REQUIRE n.record_id IS UNIQUE",
        ]
        assert all("IF NOT EXISTS" in statement for statement in statements)
        executed = [query for query, _ in driver.calls]
        assert executed == statements
        assert all(kwargs["database_"] == "neo4j-custom" for _q, kwargs in driver.calls)

    def test_ensure_schema_accepts_any_iterable_once(self) -> None:
        client, driver = make_client()
        statements = client.ensure_schema(iter(["Doc"]))
        assert len(statements) == 2
        assert len(driver.calls) == 2


class TestFromSettings:
    def test_builds_driver_with_uri_auth_and_database(self, monkeypatch) -> None:
        captured: dict[str, Any] = {}

        def fake_driver(uri: str, *, auth: tuple[str, str]) -> FakeDriver:
            captured["uri"] = uri
            captured["auth"] = auth
            return FakeDriver()

        monkeypatch.setattr("neo4j.GraphDatabase.driver", fake_driver)
        settings = Settings(
            github_token="t",
            neo4j_uri="neo4j+s://example",
            neo4j_user="neo4j",
            neo4j_password="secret",
            neo4j_database="whycode",
            llm_api_key="k",
        )
        client = Neo4jClient.from_settings(settings)
        assert captured == {
            "uri": "neo4j+s://example",
            "auth": ("neo4j", "secret"),
        }
        assert client.database == "whycode"


class TestBatchSize:
    def test_default_batch_is_in_the_recommended_range(self) -> None:
        assert MIN_BATCH_SIZE <= DEFAULT_BATCH_SIZE <= MAX_BATCH_SIZE
