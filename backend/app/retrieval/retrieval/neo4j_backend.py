"""Neo4j implementation of the retrieval graph boundary.

The four retrieval stages never build Cypher. This module owns the static
queries they share, keeps every user value in a parameter, and maps Neo4j
records onto the transport-neutral retrieval models.

Read-only by construction: the only statements here are ``MATCH`` and the
index procedures ``db.index.fulltext.queryNodes`` and
``db.index.vector.queryNodes``.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.core.errors import InvalidRecordError
from app.retrieval.config import RetrievalConfig
from app.retrieval.models import (
    AnswerConfidence,
    DecisionCandidate,
    DecisionState,
    EvidenceItem,
    GraphContext,
)

# Exact identifiers are resolved to decisions through every provenance edge a
# decision has. Direct decision IDs rank first, then governed code/config,
# then evidence, PRs and incidents.
EXACT_QUERY = """
CALL {
  MATCH (d:Decision {repo: $repo})
  WHERE d.decision_id IN $identifiers
     OR d.record_id IN $identifiers
     OR d.entity_id IN $identifiers
  RETURN d, 1 AS priority
  UNION
  MATCH (d:Decision {repo: $repo})-[:GOVERNS]->(t)
  WHERE t.record_id IN $identifiers
     OR t.entity_id IN $identifiers
     OR t.key IN $identifiers
     OR t.qualified_name IN $identifiers
     OR t.path IN $identifiers
  RETURN d, 2 AS priority
  UNION
  MATCH (d:Decision {repo: $repo})-[:SUPPORTED_BY]->(e:Evidence)
  WHERE e.evidence_id IN $identifiers OR e.record_id IN $identifiers
  RETURN d, 3 AS priority
  UNION
  MATCH (d:Decision {repo: $repo})-[:PRODUCED_IN]->(p)
  WHERE p.record_id IN $identifiers OR p.key IN $identifiers
  RETURN d, 4 AS priority
  UNION
  MATCH (d:Decision {repo: $repo})-[:MOTIVATED_BY]->(i)
  WHERE i.record_id IN $identifiers OR i.key IN $identifiers
  RETURN d, 5 AS priority
}
WITH d, min(priority) AS priority
RETURN d, priority
ORDER BY priority, coalesce(d.decision_id, d.record_id)
LIMIT $limit
"""

FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('decision_fulltext', $query)
YIELD node, score
WHERE node.repo = $repo
RETURN node, score
ORDER BY score DESC, coalesce(node.decision_id, node.record_id)
LIMIT $limit
"""

VECTOR_QUERY = """
CALL db.index.vector.queryNodes('decision_embedding', $candidates, $embedding)
YIELD node, score
WHERE node.repo = $repo
RETURN node, score
ORDER BY score DESC, coalesce(node.decision_id, node.record_id)
LIMIT $limit
"""

EXPAND_QUERY = """
MATCH (d:Decision {repo: $repo})
WHERE d.decision_id = $decision_id OR d.record_id = $decision_id
OPTIONAL MATCH (d)-[:SUPPORTED_BY]->(e:Evidence)
OPTIONAL MATCH (d)-[:GOVERNS]->(t)
OPTIONAL MATCH (d)-[:PRODUCED_IN]->(p:PullRequest)
OPTIONAL MATCH (d)-[:MOTIVATED_BY]->(i:Incident)
OPTIONAL MATCH (d)-[s:SUPERSEDES|REVERSES|REVISES]->(later:Decision)
OPTIONAL MATCH (d)-[c:CONFLICTS_WITH]-(conflict:Decision)
RETURN d,
       collect(DISTINCT e) AS evidence,
       collect(DISTINCT {node: t, labels: labels(t)}) AS targets,
       collect(DISTINCT p) AS pull_requests,
       collect(DISTINCT i) AS incidents,
       collect(DISTINCT {
         node: later,
         relation: type(s),
         reason: s.reason
       }) AS later_decisions,
       collect(DISTINCT {
         node: conflict,
         relation: type(c),
         reason: c.reason
       }) AS conflicts
"""

EVIDENCE_QUERY = """
MATCH (e:Evidence {repo: $repo})
WHERE e.evidence_id = $evidence_id OR e.record_id = $evidence_id
RETURN e
LIMIT 1
"""

HISTORY_QUERY = """
MATCH (n)
WHERE n.repo = $repo
  AND (
    n.record_id = $entity_id
    OR n.entity_id = $entity_id
    OR n.key = $entity_id
    OR n.qualified_name = $entity_id
    OR n.path = $entity_id
  )
MATCH (d:Decision {repo: $repo})
WHERE d = n
   OR (d)-[:GOVERNS]->(n)
   OR (d)-[:SUPPORTED_BY]->(n)
   OR (d)-[:PRODUCED_IN]->(n)
   OR (d)-[:MOTIVATED_BY]->(n)
RETURN DISTINCT d
ORDER BY coalesce(d.decision_id, d.record_id)
LIMIT $limit
"""

_VECTOR_OVERFETCH = 4
_HISTORY_LIMIT = 50
_COMMIT_URL = re.compile(r"/commit/(?P<sha>[0-9a-fA-F]{7,40})")
_ISSUE_URL = re.compile(r"/issues/(?P<number>\d+)")

_STATUSES = {member.value: member for member in DecisionState}
_CONFIDENCE = {
    "high": AnswerConfidence.HIGH,
    "medium": AnswerConfidence.MEDIUM,
    "low": AnswerConfidence.LOW,
    # Intent confidence has an ``insufficient`` level that the answer layer
    # treats as no confidence rather than a fourth grade.
    "insufficient": AnswerConfidence.NONE,
    "none": AnswerConfidence.NONE,
}


class QueryResultLike(Protocol):
    @property
    def records(self) -> Sequence[Any]: ...


class QueryRunner(Protocol):
    """The subset of ``Neo4jClient`` the retrieval backend needs."""

    def run(self, query: str, **parameters: Any) -> QueryResultLike: ...


class Neo4jGraphBackend:
    """Read-only Neo4j adapter for exact, full-text, vector and graph search."""

    def __init__(
        self,
        runner: QueryRunner,
        *,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.runner = runner
        self.config = config or RetrievalConfig()

    # -- retrieval sources ------------------------------------------------
    def exact_search(
        self, *, repo: str, identifiers: Sequence[str], limit: int
    ) -> Sequence[DecisionCandidate]:
        cleaned = _identifiers(identifiers)
        if not cleaned:
            return []
        rows = self._run(
            EXACT_QUERY,
            repo=repo,
            identifiers=cleaned,
            limit=limit,
        )
        return _candidates(rows, key="d")

    def fulltext_search(
        self, *, repo: str, query: str, limit: int
    ) -> Sequence[DecisionCandidate]:
        rows = self._run(
            FULLTEXT_QUERY,
            repo=repo,
            query=query,
            limit=limit,
        )
        return _candidates(rows, key="node")

    def vector_search(
        self, *, repo: str, embedding: Sequence[float], limit: int
    ) -> Sequence[DecisionCandidate]:
        _validate_embedding(embedding, dimensions=self.config.embedding_dimensions)
        rows = self._run(
            VECTOR_QUERY,
            repo=repo,
            embedding=list(embedding),
            candidates=max(limit, limit * _VECTOR_OVERFETCH),
            limit=limit,
        )
        return _candidates(rows, key="node")

    # -- graph expansion --------------------------------------------------
    def expand(self, *, repo: str, decision_id: str) -> GraphContext | None:
        rows = self._run(EXPAND_QUERY, repo=repo, decision_id=decision_id)
        if not rows:
            return None
        row = _mapping(rows[0])
        if row is None:
            return None
        candidate = _candidate(row.get("d"))
        if candidate is None:
            return None

        targets = _target_refs(row.get("targets"))
        evidence = [
            item
            for item in (
                _evidence(node, decision_id=candidate.decision_id)
                for node in _nodes(row.get("evidence"))
            )
            if item is not None
        ]
        later = _related_ids(row.get("later_decisions"))
        conflicts = _related_ids(row.get("conflicts"))
        evidence_ids = [
            item.evidence_id for item in sorted(evidence, key=lambda e: e.evidence_id)
        ]
        candidate = candidate.model_copy(
            update={
                "target_ids": targets or candidate.target_ids,
                "evidence_ids": evidence_ids or candidate.evidence_ids,
            }
        )
        return GraphContext(
            decision=candidate,
            evidence=sorted(evidence, key=lambda item: item.evidence_id),
            targets=targets,
            pull_requests=_record_ids(row.get("pull_requests")),
            incidents=_record_ids(row.get("incidents")),
            later_decisions=later,
            conflicts=conflicts,
        )

    # -- lookup endpoints -------------------------------------------------
    def get_decision(
        self, *, repo: str, decision_id: str
    ) -> GraphContext | None:
        return self.expand(repo=repo, decision_id=decision_id)

    def get_evidence(
        self, *, repo: str, evidence_id: str
    ) -> EvidenceItem | None:
        rows = self._run(EVIDENCE_QUERY, repo=repo, evidence_id=evidence_id)
        if not rows:
            return None
        row = _mapping(rows[0])
        if row is None:
            return None
        return _evidence(row.get("e"))

    def entity_history(
        self, *, repo: str, entity_id: str
    ) -> Sequence[GraphContext]:
        rows = self._run(
            HISTORY_QUERY,
            repo=repo,
            entity_id=entity_id,
            limit=_HISTORY_LIMIT,
        )
        contexts: list[GraphContext] = []
        for row in rows:
            mapping = _mapping(row)
            if mapping is None:
                continue
            candidate = _candidate(mapping.get("d"))
            if candidate is None:
                continue
            context = self.expand(
                repo=repo, decision_id=candidate.decision_id
            )
            if context is not None:
                contexts.append(context)
        return contexts

    # -- helpers ----------------------------------------------------------
    def _run(self, statement: str, **parameters: Any) -> Sequence[Any]:
        return self.runner.run(statement, **parameters).records


def _validate_embedding(
    embedding: Sequence[float], *, dimensions: int
) -> None:
    if not embedding:
        raise InvalidRecordError(
            "embedding provider returned an empty vector", component="retrieval"
        )
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value)
        for value in embedding
    ):
        raise InvalidRecordError(
            "embedding provider returned a non-finite vector",
            component="retrieval",
        )
    if len(embedding) != dimensions:
        raise InvalidRecordError(
            "embedding has "
            f"{len(embedding)} dimensions but the index expects {dimensions}",
            component="retrieval",
        )


def _identifiers(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    cleaned: list[str] = []
    for value in values:
        if not isinstance(value, str):
            continue
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


def _candidates(
    rows: Sequence[Any], *, key: str
) -> list[DecisionCandidate]:
    candidates: list[DecisionCandidate] = []
    seen: set[str] = set()
    for row in rows:
        mapping = _mapping(row)
        if mapping is None:
            continue
        node = mapping.get(key, mapping)
        candidate = _candidate(node)
        if candidate is None or candidate.decision_id in seen:
            continue
        seen.add(candidate.decision_id)
        candidates.append(candidate)
    return candidates


def _candidate(value: Any) -> DecisionCandidate | None:
    props = _mapping(value)
    if props is None:
        return None
    decision_id = _text(props.get("decision_id")) or _text(
        props.get("record_id")
    )
    if decision_id is None:
        return None
    status = _STATUSES.get(
        (_text(props.get("status")) or "").casefold(), DecisionState.ACTIVE
    )
    return DecisionCandidate(
        decision_id=decision_id,
        claim=_text(props.get("claim")) or "",
        rationale=_text(props.get("rationale")) or "",
        status=status,
        confidence=_confidence(props),
        evidence_ids=_strings(props.get("evidence_ids")),
        superseded_by=_text(props.get("superseded_by")),
    )


def _confidence(props: Mapping[str, Any]) -> AnswerConfidence:
    raw = _text(props.get("confidence"))
    label = _text(props.get("confidence_label"))
    for value in (label, raw):
        if value is None:
            continue
        mapped = _CONFIDENCE.get(value.casefold())
        if mapped is not None:
            return mapped
    return AnswerConfidence.MEDIUM


def _evidence(
    value: Any, *, decision_id: str = ""
) -> EvidenceItem | None:
    props = _mapping(value)
    if props is None:
        return None
    evidence_id = _text(props.get("evidence_id")) or _text(props.get("record_id"))
    if evidence_id is None:
        return None
    url = _text(props.get("url"))
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=(
            _text(props.get("kind"))
            or _text(props.get("record_type"))
            or "evidence"
        ),
        excerpt=_text(props.get("excerpt")) or "",
        url=url,
        decision_id=decision_id,
        pr_number=_int_or_none(props.get("pr_number")),
        issue_number=(
            _int_or_none(props.get("issue_number"))
            or _issue_number_from_url(url)
        ),
        commit_sha=(
            _text(props.get("commit_sha"))
            or _text(props.get("sha"))
            or _commit_sha_from_url(url)
        ),
        author=_text(props.get("author")),
        created_at=_text(props.get("created_at")),
        path=_text(props.get("path")),
        start_line=_positive_int_or_none(props.get("start_line")),
        end_line=_positive_int_or_none(props.get("end_line")),
    )


def _commit_sha_from_url(url: str | None) -> str | None:
    if not url:
        return None
    match = _COMMIT_URL.search(url)
    return match.group("sha") if match else None


def _issue_number_from_url(url: str | None) -> int | None:
    if not url:
        return None
    match = _ISSUE_URL.search(url)
    return int(match.group("number")) if match else None


def _target_refs(value: Any) -> list[str]:
    refs: list[str] = []
    for item in _items(value):
        ref = _target_ref(item)
        if ref is not None:
            refs.append(ref)
    return sorted(dict.fromkeys(refs))


def _target_ref(value: Any) -> str | None:
    labels: list[str] = []
    node: Any = value
    mapping = _mapping(value)
    if mapping is not None and "node" in mapping:
        node = mapping.get("node")
        labels = _labels(mapping.get("labels"))
    props = _mapping(node)
    if props is None:
        return None
    if not labels:
        labels = _labels(props.get("labels"))
    label = labels[0] if labels else _infer_label(props)
    identifier = _target_identifier(label, props)
    return f"{label}:{identifier}" if identifier else None


def _target_identifier(label: str, props: Mapping[str, Any]) -> str | None:
    if label == "CodeRegion":
        path = _text(props.get("path"))
        start = _int_or_none(props.get("start_line"))
        end = _int_or_none(props.get("end_line"))
        if path and start is not None and end is not None:
            return f"{path}:{start}-{end}"
    if label == "Symbol":
        for key in ("qualified_name", "qualname", "name"):
            if (value := _text(props.get(key))) is not None:
                return value
    if label == "ConfigKey":
        return _text(props.get("key")) or _text(props.get("record_id"))
    if label == "File":
        return _text(props.get("path")) or _text(props.get("record_id"))
    for key in (
        "record_id",
        "entity_id",
        "key",
        "qualified_name",
        "path",
        "name",
    ):
        if (value := _text(props.get(key))) is not None:
            return value
    return None


def _infer_label(props: Mapping[str, Any]) -> str:
    record_id = _text(props.get("record_id")) or ""
    if ":symbol:" in record_id or "qualified_name" in props:
        return "Symbol"
    if ":config:" in record_id or "key" in props:
        return "ConfigKey"
    if ":region:" in record_id or "start_line" in props:
        return "CodeRegion"
    if ":file:" in record_id or "commit_sha" in props:
        return "File"
    if ":pr:" in record_id:
        return "PullRequest"
    if ":commit:" in record_id:
        return "Commit"
    if ":issue:" in record_id:
        return "Issue"
    return "Node"


def _related_ids(value: Any) -> list[str]:
    ids: list[str] = []
    for item in _items(value):
        mapping = _mapping(item)
        node: Any = mapping.get("node") if mapping is not None else item
        props = _mapping(node)
        if props is None:
            continue
        identifier = (
            _text(props.get("decision_id"))
            or _text(props.get("record_id"))
        )
        if identifier is not None:
            ids.append(identifier)
    return sorted(dict.fromkeys(ids))


def _record_ids(value: Any) -> list[str]:
    ids = [
        candidate
        for item in _items(value)
        if (candidate := _record_id(item)) is not None
    ]
    return sorted(dict.fromkeys(ids))


def _record_id(value: Any) -> str | None:
    props = _mapping(value)
    if props is None:
        return None
    return _text(props.get("record_id")) or _text(props.get("key"))


def _nodes(value: Any) -> list[Any]:
    return [item for item in _items(value) if _mapping(item) is not None]


def _items(value: Any) -> list[Any]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return []
    if isinstance(value, Sequence):
        return list(value)
    return []


def _mapping(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _strings(value: Any) -> list[str]:
    if value is None or isinstance(value, (str, bytes, Mapping)):
        return []
    if not isinstance(value, Sequence):
        return []
    result: list[str] = []
    for item in value:
        text = _text(item)
        if text is not None:
            result.append(text)
    return result


def _labels(value: Any) -> list[str]:
    return _strings(value)


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _positive_int_or_none(value: Any) -> int | None:
    number = _int_or_none(value)
    return number if number is not None and number > 0 else None


__all__ = [
    "EXACT_QUERY",
    "EXPAND_QUERY",
    "EVIDENCE_QUERY",
    "FULLTEXT_QUERY",
    "HISTORY_QUERY",
    "Neo4jGraphBackend",
    "QueryRunner",
    "VECTOR_QUERY",
]
