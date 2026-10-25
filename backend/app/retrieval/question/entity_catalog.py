"""The set of entities a question may resolve to.

The catalog is an allowlist. Phase 1 never creates an entity from model text;
it only selects an entry that already exists in the graph. The loader is
read-only and repository-scoped, so the same questions cannot leak across
repositories.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from app.core.errors import InvalidRecordError
from app.retrieval.question.models import CatalogEntity, EntityType

_KNOWN_INTENT_LABELS = ("Decision", "Constraint", "Reversal", "Incident")


class QueryResultLike(Protocol):
    @property
    def records(self) -> Sequence[Any]: ...


class QueryRunner(Protocol):
    """The subset of ``Neo4jClient`` this package needs."""

    def run(self, query: str, **parameters: Any) -> QueryResultLike: ...


class EntityCatalog(Protocol):
    repo: str

    def entries(self) -> Sequence[CatalogEntity]: ...


class StaticEntityCatalog:
    """An in-memory catalog, used by tests and offline fixtures."""

    def __init__(self, repo: str, entities: Sequence[CatalogEntity]) -> None:
        if not repo.strip():
            raise InvalidRecordError(
                "catalog repo must not be empty", component="retrieval"
            )
        seen: set[tuple[EntityType, str]] = set()
        for entity in entities:
            key = (entity.type, entity.graph_key)
            if key in seen:
                raise InvalidRecordError(
                    f"duplicate catalog entry {entity.type.value}:{entity.graph_key}",
                    component="retrieval",
                )
            seen.add(key)
        self.repo = repo
        self._entries = tuple(
            sorted(
                entities,
                key=lambda item: (
                    item.type.value,
                    item.identifier.casefold(),
                    item.graph_key,
                ),
            )
        )

    def entries(self) -> Sequence[CatalogEntity]:
        return self._entries


_SYMBOLS = """
MATCH (n:Symbol {repo: $repo})
RETURN n.record_id AS graph_key,
       coalesce(n.qualified_name, n.qualname, n.name) AS identifier,
       n.name AS display_name,
       n.qualname AS qualname,
       n.path AS path
LIMIT $limit
"""

_CONFIG_KEYS = """
MATCH (n:ConfigKey {repo: $repo})
RETURN n.record_id AS graph_key,
       n.key AS identifier,
       n.source_path AS path
LIMIT $limit
"""

_FILES = """
MATCH (n:File {repo: $repo})
RETURN n.record_id AS graph_key,
       n.path AS identifier,
       n.path AS path
LIMIT $limit
"""

_DECISIONS = """
MATCH (n)
WHERE n.repo = $repo
  AND (n:Decision OR n:Constraint OR n:Reversal OR n:Incident)
RETURN n.record_id AS graph_key,
       coalesce(n.decision_id, n.record_id) AS identifier,
       n.claim AS claim,
       n.status AS status,
       n.pr_number AS pr_number,
       labels(n) AS labels
LIMIT $limit
"""

_PEOPLE = """
MATCH (n:Person {repo: $repo})
RETURN n.record_id AS graph_key,
       n.login AS identifier
LIMIT $limit
"""


class Neo4jEntityCatalog:
    """Read a repository's catalog from Neo4j once per process or request."""

    @classmethod
    def load(
        cls,
        runner: QueryRunner,
        repo: str,
        *,
        limit_per_type: int = 5000,
    ) -> StaticEntityCatalog:
        if not repo.strip():
            raise InvalidRecordError(
                "catalog repo must not be empty", component="retrieval"
            )
        if limit_per_type < 1:
            raise InvalidRecordError(
                "catalog limit_per_type must be positive", component="retrieval"
            )
        entities: list[CatalogEntity] = []
        entities.extend(
            _load(
                runner,
                repo,
                _SYMBOLS,
                limit_per_type,
                _symbol_entity,
            )
        )
        entities.extend(
            _load(
                runner,
                repo,
                _CONFIG_KEYS,
                limit_per_type,
                _config_entity,
            )
        )
        entities.extend(
            _load(
                runner,
                repo,
                _FILES,
                limit_per_type,
                _file_entity,
            )
        )
        entities.extend(
            _load(
                runner,
                repo,
                _DECISIONS,
                limit_per_type,
                _decision_entity,
            )
        )
        entities.extend(
            _load(
                runner,
                repo,
                _PEOPLE,
                limit_per_type,
                _person_entity,
            )
        )
        return StaticEntityCatalog(repo, entities)


def _load(
    runner: QueryRunner,
    repo: str,
    query: str,
    limit: int,
    build: Callable[[Mapping[str, Any]], CatalogEntity | None],
) -> list[CatalogEntity]:
    result = runner.run(query, repo=repo, limit=limit)
    entities: list[CatalogEntity] = []
    for raw in result.records:
        if not isinstance(raw, Mapping):
            continue
        entity = build(raw)
        if entity is not None:
            entities.append(entity)
    return entities


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _graph_key(row: Mapping[str, Any]) -> str | None:
    return _text(row.get("graph_key"))


def _symbol_entity(row: Mapping[str, Any]) -> CatalogEntity | None:
    graph_key = _graph_key(row)
    identifier = _text(row.get("identifier"))
    if graph_key is None or identifier is None:
        return None
    display_name = _text(row.get("display_name"))
    qualname = _text(row.get("qualname"))
    aliases = _dedupe_aliases(
        identifier,
        display_name,
        qualname,
        identifier.rsplit(".", 1)[-1],
    )
    return CatalogEntity(
        graph_key=graph_key,
        type=EntityType.SYMBOL,
        identifier=identifier,
        display_name=display_name,
        path=_text(row.get("path")),
        aliases=aliases,
    )


def _config_entity(row: Mapping[str, Any]) -> CatalogEntity | None:
    graph_key = _graph_key(row)
    identifier = _text(row.get("identifier"))
    if graph_key is None or identifier is None:
        return None
    aliases = _dedupe_aliases(
        identifier,
        identifier.rsplit(".", 1)[-1],
        identifier.rsplit("_", 1)[-1],
    )
    return CatalogEntity(
        graph_key=graph_key,
        type=EntityType.CONFIG_KEY,
        identifier=identifier,
        path=_text(row.get("path")),
        aliases=aliases,
    )


def _file_entity(row: Mapping[str, Any]) -> CatalogEntity | None:
    graph_key = _graph_key(row)
    identifier = _text(row.get("identifier"))
    if graph_key is None or identifier is None:
        return None
    aliases = _dedupe_aliases(identifier, identifier.rsplit("/", 1)[-1])
    return CatalogEntity(
        graph_key=graph_key,
        type=EntityType.FILE,
        identifier=identifier,
        path=_text(row.get("path")) or identifier,
        aliases=aliases,
    )


def _decision_entity(row: Mapping[str, Any]) -> CatalogEntity | None:
    graph_key = _graph_key(row)
    identifier = _text(row.get("identifier"))
    if graph_key is None or identifier is None:
        return None
    labels = row.get("labels")
    source_label = None
    if isinstance(labels, (list, tuple)):
        for label in labels:
            if isinstance(label, str) and label in _KNOWN_INTENT_LABELS:
                source_label = label
                break
    return CatalogEntity(
        graph_key=graph_key,
        type=EntityType.DECISION,
        identifier=identifier,
        claim=_text(row.get("claim")),
        status=_text(row.get("status")),
        pr_number=(
            row.get("pr_number")
            if isinstance(row.get("pr_number"), int)
            else None
        ),
        source_label=source_label,
    )


def _person_entity(row: Mapping[str, Any]) -> CatalogEntity | None:
    graph_key = _graph_key(row)
    identifier = _text(row.get("identifier"))
    if graph_key is None or identifier is None:
        return None
    return CatalogEntity(
        graph_key=graph_key,
        type=EntityType.PERSON,
        identifier=identifier,
        display_name=identifier,
    )


def _dedupe_aliases(*values: str | None) -> tuple[str, ...]:
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        if value is None:
            continue
        candidate = value.strip()
        if len(candidate) < 2:
            continue
        folded = candidate.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        aliases.append(candidate)
    return tuple(aliases)


__all__ = [
    "EntityCatalog",
    "Neo4jEntityCatalog",
    "QueryResultLike",
    "QueryRunner",
    "StaticEntityCatalog",
]
