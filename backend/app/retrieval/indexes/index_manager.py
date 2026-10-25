"""Neo4j index definitions used by hybrid retrieval.

Index creation is idempotent and happens during component setup, never on
the request path. The statements are data so they can be reviewed and tested
without a live database.

Full-text indexes cover the lexical fields. Vector indexes are declared for
every object the retrieval pipeline may embed; the embedding text is chosen
once here and reused by the write-time embedding step.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.retrieval.config import RetrievalConfig
from app.retrieval.ports import IndexExecutor

IndexKind = Literal["fulltext", "vector"]


@dataclass(frozen=True)
class IndexDefinition:
    name: str
    kind: IndexKind
    cypher: str
    label: str | None = None
    properties: tuple[str, ...] = ()


@dataclass(frozen=True)
class VectorTarget:
    """One node label and the properties that make up its embedding text."""

    name: str
    label: str
    properties: tuple[str, ...]
    variable: str = "n"


VECTOR_TARGETS: tuple[VectorTarget, ...] = (
    VectorTarget(
        name="decision_embedding",
        label="Decision",
        variable="d",
        properties=("claim", "rationale"),
    ),
    VectorTarget(
        name="evidence_embedding",
        label="Evidence",
        variable="e",
        properties=("excerpt",),
    ),
    VectorTarget(
        name="symbol_embedding",
        label="Symbol",
        variable="s",
        properties=("qualified_name", "docstring"),
    ),
    VectorTarget(
        name="config_embedding",
        label="ConfigKey",
        variable="c",
        properties=("key",),
    ),
    VectorTarget(
        name="code_region_embedding",
        label="CodeRegion",
        variable="r",
        properties=("excerpt",),
    ),
)

VECTOR_TARGET_BY_LABEL: dict[str, VectorTarget] = {
    target.label: target for target in VECTOR_TARGETS
}

FULLTEXT_INDEXES: tuple[IndexDefinition, ...] = (
    IndexDefinition(
        name="decision_fulltext",
        kind="fulltext",
        label="Decision",
        properties=("claim", "rationale"),
        cypher=(
            "CREATE FULLTEXT INDEX decision_fulltext IF NOT EXISTS\n"
            "FOR (d:Decision)\n"
            "ON EACH [d.claim, d.rationale]"
        ),
    ),
    IndexDefinition(
        name="evidence_fulltext",
        kind="fulltext",
        label="Evidence",
        properties=("excerpt",),
        cypher=(
            "CREATE FULLTEXT INDEX evidence_fulltext IF NOT EXISTS\n"
            "FOR (e:Evidence)\n"
            "ON EACH [e.excerpt]"
        ),
    ),
    IndexDefinition(
        name="symbol_fulltext",
        kind="fulltext",
        label="Symbol",
        properties=("name", "qualified_name"),
        cypher=(
            "CREATE FULLTEXT INDEX symbol_fulltext IF NOT EXISTS\n"
            "FOR (s:Symbol)\n"
            "ON EACH [s.name, s.qualified_name]"
        ),
    ),
    IndexDefinition(
        name="config_fulltext",
        kind="fulltext",
        label="ConfigKey",
        properties=("key",),
        cypher=(
            "CREATE FULLTEXT INDEX config_fulltext IF NOT EXISTS\n"
            "FOR (c:ConfigKey)\n"
            "ON EACH [c.key]"
        ),
    ),
)


def vector_index_definition(
    target: VectorTarget, config: RetrievalConfig
) -> IndexDefinition:
    return IndexDefinition(
        name=target.name,
        kind="vector",
        label=target.label,
        properties=("embedding",),
        cypher=(
            f"CREATE VECTOR INDEX {target.name} IF NOT EXISTS\n"
            f"FOR ({target.variable}:{target.label})\n"
            f"ON ({target.variable}.embedding)\n"
            "OPTIONS {\n"
            "  indexConfig: {\n"
            f"    `vector.dimensions`: {config.embedding_dimensions},\n"
            "    `vector.similarity_function`: "
            f"'{config.vector_similarity}'\n"
            "  }\n"
            "}"
        ),
    )


def vector_indexes(
    config: RetrievalConfig | None = None,
) -> tuple[IndexDefinition, ...]:
    settings = config or RetrievalConfig()
    return tuple(
        vector_index_definition(target, settings) for target in VECTOR_TARGETS
    )


class IndexManager:
    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig()

    def definitions(self) -> list[IndexDefinition]:
        return [*FULLTEXT_INDEXES, *vector_indexes(self.config)]

    def ensure(self, executor: IndexExecutor) -> list[str]:
        names: list[str] = []
        for definition in self.definitions():
            executor.run(definition.cypher)
            names.append(definition.name)
        return names

    def names(self) -> list[str]:
        return [definition.name for definition in self.definitions()]


__all__ = [
    "FULLTEXT_INDEXES",
    "IndexDefinition",
    "IndexKind",
    "IndexManager",
    "VECTOR_TARGETS",
    "VECTOR_TARGET_BY_LABEL",
    "VectorTarget",
    "vector_index_definition",
    "vector_indexes",
]
