"""Small, dependency-free fakes for the retrieval pipeline.

Every test gets a graph backend, an embedding provider and an answer model
that are plain Python. No Neo4j, no network, no clock.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.retrieval.models import (
    AnswerConfidence,
    DecisionCandidate,
    DecisionState,
    EvidenceItem,
    ExtractedEntity,
    GraphContext,
    MatchSource,
    QuestionType,
    QuestionUnderstanding,
    RankedCandidate,
    RetrievalSource,
)

REPO = "encode/httpx"
DECISION_ID = "dec_001"
SECOND_DECISION_ID = "dec_002"
CONFIG_ID = "retry.max_attempts"
SYMBOL_ID = "Client.send"
FILE_ID = "httpx/_client.py"


def candidate(
    decision_id: str = DECISION_ID,
    *,
    claim: str = "HTTPX does not retry requests by default.",
    rationale: str = "Automatic retries can duplicate non-idempotent requests.",
    status: DecisionState = DecisionState.ACTIVE,
    confidence: AnswerConfidence = AnswerConfidence.HIGH,
    target_ids: Sequence[str] | None = None,
    evidence_ids: Sequence[str] | None = None,
    sources: dict[RetrievalSource, int] | None = None,
    superseded_by: str | None = None,
) -> DecisionCandidate:
    return DecisionCandidate(
        decision_id=decision_id,
        claim=claim,
        rationale=rationale,
        status=status,
        confidence=confidence,
        target_ids=list(
            target_ids
            if target_ids is not None
            else [f"ConfigKey:{CONFIG_ID}", f"Symbol:{SYMBOL_ID}"]
        ),
        evidence_ids=list(
            evidence_ids if evidence_ids is not None else ["ev_101", "ev_102"]
        ),
        superseded_by=superseded_by,
        sources=sources or {},
    )


def evidence(
    evidence_id: str = "ev_101",
    *,
    kind: str = "review_comment",
    excerpt: str = "Unbounded retries can amplify failures during an outage.",
    decision_id: str = DECISION_ID,
    url: str | None = "https://github.com/encode/httpx/pull/1234#discussion_r1",
    pr_number: int | None = 1234,
    issue_number: int | None = None,
    commit_sha: str | None = None,
    author: str | None = "alice",
    path: str | None = "httpx/_client.py",
    start_line: int | None = 312,
    end_line: int | None = 318,
    created_at: str | None = "2024-03-30T09:05:00Z",
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        excerpt=excerpt,
        url=url,
        decision_id=decision_id,
        pr_number=pr_number,
        issue_number=issue_number,
        commit_sha=commit_sha,
        author=author,
        created_at=created_at,
        path=path,
        start_line=start_line,
        end_line=end_line,
    )


def review_and_commit(decision_id: str = DECISION_ID) -> list[EvidenceItem]:
    return [
        evidence(decision_id=decision_id),
        evidence(
            "ev_102",
            kind="commit",
            excerpt=(
                "Disable automatic retries to avoid duplicating non-idempotent "
                "requests."
            ),
            decision_id=decision_id,
            author="bob",
            path=None,
            start_line=None,
            end_line=None,
            url=None,
        ),
    ]


def context(
    item: DecisionCandidate | None = None,
    *,
    evidence_items: Sequence[EvidenceItem] | None = None,
    targets: Sequence[str] | None = None,
    pull_requests: Sequence[str] | None = None,
    incidents: Sequence[str] | None = None,
    later_decisions: Sequence[str] | None = None,
    conflicts: Sequence[str] | None = None,
) -> GraphContext:
    decision = item or candidate()
    return GraphContext(
        decision=decision,
        evidence=list(
            evidence_items
            if evidence_items is not None
            else review_and_commit(decision.decision_id)
        ),
        targets=list(
            targets
            if targets is not None
            else [
                f"Symbol:{SYMBOL_ID}",
                f"ConfigKey:{CONFIG_ID}",
                f"CodeRegion:{FILE_ID}:310-325",
                "Commit:abc123",
            ]
        ),
        pull_requests=list(
            pull_requests if pull_requests is not None else ["PullRequest:#1234"]
        ),
        incidents=list(incidents or []),
        later_decisions=list(later_decisions or []),
        conflicts=list(conflicts or []),
    )


def entity(
    entity_type: str,
    identifier: str,
    *,
    text: str | None = None,
    match: str | None = None,
) -> ExtractedEntity:
    return ExtractedEntity(
        type=_entity_type(entity_type),
        text=text or identifier,
        normalized=identifier,
        match=_match_source(match) if match else None,
    )


def _match_source(value: str) -> MatchSource:
    return {
        "exact": MatchSource.EXACT,
        "lexical": MatchSource.LEXICAL,
        "llm": MatchSource.LLM,
    }[value]


def _entity_type(value: str) -> Any:
    from app.retrieval.models import EntityType

    aliases = {
        "config": EntityType.CONFIG_KEY,
        "config_key": EntityType.CONFIG_KEY,
        "symbol": EntityType.SYMBOL,
        "file": EntityType.FILE,
        "decision": EntityType.DECISION,
    }
    return aliases.get(value, EntityType.SYMBOL)


def understanding(
    question: str = "Why are retries disabled by default in httpx?",
    *,
    question_type: QuestionType = QuestionType.CONFIG_WHY,
    entities: Sequence[ExtractedEntity] | None = None,
    time_constraint: str | None = None,
    requires_supersession_check: bool = True,
) -> QuestionUnderstanding:
    return QuestionUnderstanding(
        question=question,
        question_type=question_type,
        entities=list(
            entities
            if entities is not None
            else [entity("config", CONFIG_ID, text="retry limit")]
        ),
        time_constraint=time_constraint,
        requires_supersession_check=requires_supersession_check,
    )


def ranked(
    item: DecisionCandidate | None = None,
    *,
    fused_score: float = 0.07,
    final_score: float = 0.09,
) -> RankedCandidate:
    from app.retrieval.models import RerankSignals

    return RankedCandidate(
        candidate=item or candidate(),
        fused_score=fused_score,
        final_score=final_score,
        signals=RerankSignals(
            graph_path_to_exact_entity=True,
            independent_evidence_sources=2,
            target_matches_entity=True,
        ),
        reasons=["direct graph path to the asked entity"],
    )


class FakeBackend:
    def __init__(
        self,
        *,
        exact: Sequence[DecisionCandidate] = (),
        fulltext: Sequence[DecisionCandidate] = (),
        vector: Sequence[DecisionCandidate] = (),
        contexts: dict[str, GraphContext] | None = None,
        honor_limit: bool = True,
    ) -> None:
        self.exact_results = list(exact)
        self.fulltext_results = list(fulltext)
        self.vector_results = list(vector)
        self.contexts = dict(contexts or {})
        self.honor_limit = honor_limit
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._lock = threading.Lock()

    def _record(self, name: str, **payload: Any) -> None:
        with self._lock:
            self.calls.append((name, payload))

    def exact_search(
        self, *, repo: str, identifiers: Sequence[str], limit: int
    ) -> Sequence[DecisionCandidate]:
        self._record("exact", repo=repo, identifiers=list(identifiers), limit=limit)
        rows = list(self.exact_results)
        return rows[:limit] if self.honor_limit else rows

    def fulltext_search(
        self, *, repo: str, query: str, limit: int
    ) -> Sequence[DecisionCandidate]:
        self._record("fulltext", repo=repo, query=query, limit=limit)
        rows = list(self.fulltext_results)
        return rows[:limit] if self.honor_limit else rows

    def vector_search(
        self, *, repo: str, embedding: Sequence[float], limit: int
    ) -> Sequence[DecisionCandidate]:
        self._record("vector", repo=repo, embedding=list(embedding), limit=limit)
        rows = list(self.vector_results)
        return rows[:limit] if self.honor_limit else rows

    def expand(self, *, repo: str, decision_id: str) -> GraphContext | None:
        self._record("expand", repo=repo, decision_id=decision_id)
        return self.contexts.get(decision_id)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeEmbedder:
    def __init__(self, vector: Sequence[float] = (0.1, 0.2, 0.3)) -> None:
        self.vector = list(vector)
        self.calls: list[str] = []

    def embed(self, text: str) -> list[float]:
        self.calls.append(text)
        return list(self.vector)


@dataclass
class GeneratorCall:
    system: str
    user: str
    schema: dict[str, Any]
    schema_name: str


@dataclass
class FakeGenerator:
    responses: dict[str, list[str] | str] = field(default_factory=dict)
    repeat: bool = False
    calls: list[GeneratorCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._queues: dict[str, list[str]] = {}
        for name, value in self.responses.items():
            self._queues[name] = [value] if isinstance(value, str) else list(value)

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        self.calls.append(
            GeneratorCall(
                system=system, user=user, schema=schema, schema_name=schema_name
            )
        )
        queue = self._queues.get(schema_name)
        if not queue:
            raise AssertionError(f"no response queued for {schema_name!r}")
        return queue[0] if self.repeat else queue.pop(0)


def answer_json(
    *,
    status: str = "answered",
    answer: str = "HTTPX disables retries by default [E1].",
    citations: Sequence[str] = ("E1",),
    caveats: Sequence[str] = (),
    confidence: str = "high",
) -> str:
    return json.dumps(
        {
            "answer_status": status,
            "answer": answer,
            "citations": list(citations),
            "caveats": list(caveats),
            "confidence": confidence,
        }
    )


def entity_json(
    *,
    entities: Sequence[dict[str, str]],
    time_constraint: str | None = None,
    requires_supersession_check: bool = True,
) -> str:
    return json.dumps(
        {
            "entities": list(entities),
            "time_constraint": time_constraint,
            "requires_supersession_check": requires_supersession_check,
        }
    )


class FakeLookup:
    def __init__(
        self,
        *,
        contexts: dict[str, GraphContext] | None = None,
        evidence_by_id: dict[str, EvidenceItem] | None = None,
        history: dict[str, list[GraphContext]] | None = None,
    ) -> None:
        self.contexts = contexts or {}
        self.evidence_by_id = evidence_by_id or {}
        self.history = history or {}

    def get_decision(self, *, repo: str, decision_id: str) -> GraphContext | None:
        del repo
        return self.contexts.get(decision_id)

    def get_evidence(self, *, repo: str, evidence_id: str) -> EvidenceItem | None:
        del repo
        return self.evidence_by_id.get(evidence_id)

    def entity_history(
        self, *, repo: str, entity_id: str
    ) -> Sequence[GraphContext]:
        del repo
        return self.history.get(entity_id, [])
