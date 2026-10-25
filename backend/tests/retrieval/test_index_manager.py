"""Index definitions are reviewable data: names, labels, properties and
vector options are pinned so a typo cannot silently disable hybrid search."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.retrieval.config import RetrievalConfig
from app.retrieval.indexes.index_manager import (
    FULLTEXT_INDEXES,
    VECTOR_TARGETS,
    IndexManager,
)


class RecordingExecutor:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def run(self, cypher: str) -> None:
        self.statements.append(cypher)


def test_expected_index_names() -> None:
    assert IndexManager().names() == [
        "decision_fulltext",
        "evidence_fulltext",
        "symbol_fulltext",
        "config_fulltext",
        "decision_embedding",
        "evidence_embedding",
        "symbol_embedding",
        "config_embedding",
        "code_region_embedding",
    ]


def test_fulltext_indexes_cover_the_documented_labels_and_properties() -> None:
    by_name = {item.name: item.cypher for item in FULLTEXT_INDEXES}
    assert "FOR (d:Decision)" in by_name["decision_fulltext"]
    assert "[d.claim, d.rationale]" in by_name["decision_fulltext"]
    assert "FOR (e:Evidence)" in by_name["evidence_fulltext"]
    assert "ON EACH [e.excerpt]" in by_name["evidence_fulltext"]
    assert "FOR (s:Symbol)" in by_name["symbol_fulltext"]
    assert "[s.name, s.qualified_name]" in by_name["symbol_fulltext"]
    assert "FOR (c:ConfigKey)" in by_name["config_fulltext"]
    assert "[c.key]" in by_name["config_fulltext"]


@pytest.mark.parametrize("definition", FULLTEXT_INDEXES)
def test_every_creation_is_idempotent(definition) -> None:
    assert "IF NOT EXISTS" in definition.cypher
    assert definition.cypher.startswith("CREATE FULLTEXT INDEX")
    assert definition.name in definition.cypher


def test_vector_indexes_cover_every_embedded_label() -> None:
    expected = {
        "decision_embedding": ("d", "Decision", ("claim", "rationale")),
        "evidence_embedding": ("e", "Evidence", ("excerpt",)),
        "symbol_embedding": ("s", "Symbol", ("qualified_name", "docstring")),
        "config_embedding": ("c", "ConfigKey", ("key",)),
        "code_region_embedding": ("r", "CodeRegion", ("excerpt",)),
    }
    assert {
        target.name: (target.variable, target.label, target.properties)
        for target in VECTOR_TARGETS
    } == expected


def test_vector_index_uses_configured_dimensions_and_similarity() -> None:
    manager = IndexManager(
        RetrievalConfig(embedding_dimensions=3072, vector_similarity="cosine")
    )
    vector = [item for item in manager.definitions() if item.kind == "vector"]
    assert len(vector) == len(VECTOR_TARGETS)
    assert {item.name for item in vector} == {
        target.name for target in VECTOR_TARGETS
    }
    targets = {target.name: target for target in VECTOR_TARGETS}
    by_name = {item.name: item.cypher for item in vector}
    assert "FOR (d:Decision)" in by_name["decision_embedding"]
    assert "ON (d.embedding)" in by_name["decision_embedding"]
    for item in vector:
        target = targets[item.name]
        assert f"FOR ({target.variable}:{target.label})" in item.cypher
        assert f"ON ({target.variable}.embedding)" in item.cypher
        assert "`vector.dimensions`: 3072" in item.cypher
        assert "`vector.similarity_function`: 'cosine'" in item.cypher
        assert "IF NOT EXISTS" in item.cypher


def test_ensure_executes_every_statement_and_returns_names() -> None:
    executor = RecordingExecutor()
    manager = IndexManager()
    names = manager.ensure(executor)
    assert names == manager.names()
    assert len(executor.statements) == len(names)
    assert all("IF NOT EXISTS" in statement for statement in executor.statements)


def test_ensure_is_repeatable() -> None:
    executor = RecordingExecutor()
    manager = IndexManager()
    first = manager.ensure(executor)
    second = manager.ensure(executor)
    assert first == second
    assert len(executor.statements) == len(first) * 2


def test_invalid_embedding_dimensions_are_rejected() -> None:
    with pytest.raises(ValidationError):
        RetrievalConfig(embedding_dimensions=0)


def test_invalid_similarity_function_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RetrievalConfig(vector_similarity="hamming")  # type: ignore[arg-type]
