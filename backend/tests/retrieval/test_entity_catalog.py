"""Catalog loader contracts.

The loader is read-only, repo-scoped and parameterised. Tests use a fake
runner so no live Neo4j is required.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.errors import InvalidRecordError
from app.retrieval.question.entity_catalog import (
    Neo4jEntityCatalog,
    StaticEntityCatalog,
)
from app.retrieval.question.models import CatalogEntity, EntityType

REPO = "encode/httpx"


class Result:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, query: str, **parameters: Any) -> Result:
        self.calls.append((query, parameters))
        if "n:Symbol" in query:
            return Result(
                [
                    {
                        "graph_key": "symbol-1",
                        "identifier": "httpx._client.Client.send",
                        "display_name": "send",
                        "qualname": "Client.send",
                        "path": "httpx/_client.py",
                    },
                    {
                        "graph_key": None,
                        "identifier": "broken",
                    },
                ]
            )
        if "n:ConfigKey" in query:
            return Result(
                [
                    {
                        "graph_key": "config-1",
                        "identifier": "retry.max_attempts",
                        "path": "httpx/_config.py",
                    }
                ]
            )
        if "n:File" in query:
            return Result(
                [
                    {
                        "graph_key": "file-1",
                        "identifier": "httpx/_client.py",
                        "path": "httpx/_client.py",
                    }
                ]
            )
        if "n:Decision OR" in query:
            return Result(
                [
                    {
                        "graph_key": "decision-1",
                        "identifier": "dec_001",
                        "claim": "Retries are disabled by default.",
                        "status": "accepted",
                        "pr_number": 1525,
                        "labels": ["Decision"],
                    }
                ]
            )
        if "n:Person" in query:
            return Result(
                [{"graph_key": "person-1", "identifier": "alice"}]
            )
        raise AssertionError(f"unexpected query: {query}")


def test_loader_builds_a_typed_catalog() -> None:
    runner = FakeRunner()
    catalog = Neo4jEntityCatalog.load(runner, REPO, limit_per_type=25)
    entities = {entity.graph_key: entity for entity in catalog.entries()}
    assert catalog.repo == REPO
    assert set(entities) == {
        "symbol-1",
        "config-1",
        "file-1",
        "decision-1",
        "person-1",
    }
    symbol = entities["symbol-1"]
    assert symbol.type is EntityType.SYMBOL
    assert "Client.send" in symbol.aliases
    assert entities["config-1"].type is EntityType.CONFIG_KEY
    assert entities["file-1"].type is EntityType.FILE
    decision = entities["decision-1"]
    assert decision.type is EntityType.DECISION
    assert decision.source_label == "Decision"
    assert decision.claim == "Retries are disabled by default."
    assert entities["person-1"].type is EntityType.PERSON


def test_loader_uses_parameters_and_a_limit() -> None:
    runner = FakeRunner()
    Neo4jEntityCatalog.load(runner, REPO, limit_per_type=25)
    assert len(runner.calls) == 5
    for query, parameters in runner.calls:
        assert "$repo" in query
        assert parameters == {"repo": REPO, "limit": 25}


def test_loader_skips_rows_without_a_graph_key() -> None:
    catalog = Neo4jEntityCatalog.load(FakeRunner(), REPO)
    assert all(entity.graph_key for entity in catalog.entries())


def test_loader_requires_a_repo() -> None:
    with pytest.raises(InvalidRecordError):
        Neo4jEntityCatalog.load(FakeRunner(), "  ")


def test_static_catalog_rejects_duplicate_entries() -> None:
    entity = CatalogEntity(
        graph_key="same",
        type=EntityType.CONFIG_KEY,
        identifier="retries",
    )
    with pytest.raises(InvalidRecordError):
        StaticEntityCatalog(REPO, [entity, entity])


def test_static_catalog_sorts_entries_for_stable_output() -> None:
    catalog = StaticEntityCatalog(
        REPO,
        [
            CatalogEntity(
                graph_key="b",
                type=EntityType.SYMBOL,
                identifier="B",
            ),
            CatalogEntity(
                graph_key="a",
                type=EntityType.CONFIG_KEY,
                identifier="a",
            ),
        ],
    )
    assert [entity.graph_key for entity in catalog.entries()] == ["a", "b"]
