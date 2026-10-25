"""Neo4j backend contracts: static Cypher, parameterized values, and mapping
records onto retrieval models. No test opens a database."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pytest
from retrieval_fixtures import (
    REPO,
    FakeEmbedder,
    FakeGenerator,
    answer_json,
)

from app.core.errors import InvalidRecordError
from app.retrieval.answer.answer_synthesizer import ANSWER_SCHEMA_NAME
from app.retrieval.config import RetrievalConfig
from app.retrieval.models import (
    AnswerConfidence,
    AnswerStatus,
    AskParameters,
    DecisionState,
    EntityType,
    RetrievalSource,
)
from app.retrieval.question.entity_extractor import EntityIndex, KnownEntity
from app.retrieval.retrieval.neo4j_backend import (
    EXACT_QUERY,
    EXPAND_QUERY,
    FULLTEXT_QUERY,
    VECTOR_QUERY,
    Neo4jGraphBackend,
)
from app.retrieval.service import RetrievalService

DECISION = {
    "decision_id": "dec_001",
    "record_id": "httpx:decision:dec_001",
    "repo": REPO,
    "claim": "HTTPX does not retry requests by default.",
    "rationale": "Automatic retries can duplicate non-idempotent requests.",
    "status": "active",
    "confidence": "insufficient",
    "evidence_ids": ["ev_101", "ev_102"],
}
EVIDENCE = {
    "evidence_id": "ev_101",
    "record_id": "httpx:review_comment:11",
    "repo": REPO,
    "record_type": "review_comment",
    "excerpt": "Unbounded retries can amplify failures during an outage.",
    "url": "https://github.com/encode/httpx/pull/1234#discussion_r1",
    "pr_number": 1234,
    "author": "alice",
    "created_at": "2024-03-30T09:05:00Z",
    "path": "httpx/_client.py",
    "start_line": 312,
    "end_line": 318,
}


@dataclass
class Result:
    records: list[Any]


class FakeRunner:
    def __init__(
        self,
        *,
        exact: Sequence[Any] = (),
        fulltext: Sequence[Any] = (),
        vector: Sequence[Any] = (),
        expand: Sequence[Any] = (),
        evidence: Sequence[Any] = (),
        history: Sequence[Any] = (),
    ) -> None:
        self.exact = list(exact)
        self.fulltext = list(fulltext)
        self.vector = list(vector)
        self.expand = list(expand)
        self.evidence = list(evidence)
        self.history = list(history)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def run(self, statement: str, **parameters: Any) -> Result:
        self.calls.append((statement, parameters))
        if "db.index.fulltext.queryNodes" in statement:
            return Result(self.fulltext)
        if "db.index.vector.queryNodes" in statement:
            return Result(self.vector)
        if "CALL {" in statement:
            return Result(self.exact)
        if "OPTIONAL MATCH (d)-[:SUPPORTED_BY]" in statement:
            return Result(self.expand)
        if "MATCH (e:Evidence" in statement:
            return Result(self.evidence)
        if "MATCH (n)" in statement:
            return Result(self.history)
        raise AssertionError(f"unexpected query: {statement}")

    def only_call(self) -> tuple[str, dict[str, Any]]:
        assert len(self.calls) == 1
        return self.calls[0]


def _backend(runner: FakeRunner, **config: Any) -> Neo4jGraphBackend:
    return Neo4jGraphBackend(
        runner, config=RetrievalConfig(embedding_dimensions=3, **config)
    )


def test_exact_query_is_static_and_parameterized() -> None:
    runner = FakeRunner(exact=[{"d": DECISION, "priority": 1}])
    rows = _backend(runner).exact_search(
        repo=REPO,
        identifiers=["retry.max_attempts", "retry.max_attempts", " "],
        limit=10,
    )
    query, parameters = runner.only_call()
    assert query == EXACT_QUERY
    assert parameters == {
        "repo": REPO,
        "identifiers": ["retry.max_attempts"],
        "limit": 10,
    }
    assert "GOVERNS" in query
    assert "SUPPORTED_BY" in query
    assert "PRODUCED_IN" in query
    assert "MOTIVATED_BY" in query
    assert [row.decision_id for row in rows] == ["dec_001"]


def test_exact_search_maps_decision_fields() -> None:
    runner = FakeRunner(exact=[{"d": DECISION, "priority": 1}])
    candidate = _backend(runner).exact_search(
        repo=REPO, identifiers=["dec_001"], limit=10
    )[0]
    assert candidate.claim == DECISION["claim"]
    assert candidate.rationale == DECISION["rationale"]
    assert candidate.status is DecisionState.ACTIVE
    # Intent's "insufficient" confidence is not a fourth answer grade.
    assert candidate.confidence is AnswerConfidence.NONE
    assert candidate.evidence_ids == ["ev_101", "ev_102"]
    assert candidate.sources == {}


def test_exact_search_skips_rows_without_a_decision_id() -> None:
    runner = FakeRunner(
        exact=[
            {"d": {"claim": "No identity."}, "priority": 1},
            {"d": DECISION, "priority": 2},
        ]
    )
    rows = _backend(runner).exact_search(
        repo=REPO, identifiers=["dec_001"], limit=10
    )
    assert [row.decision_id for row in rows] == ["dec_001"]


def test_exact_search_deduplicates_repeated_decisions() -> None:
    runner = FakeRunner(
        exact=[{"d": DECISION, "priority": 1}, {"d": DECISION, "priority": 2}]
    )
    rows = _backend(runner).exact_search(
        repo=REPO, identifiers=["dec_001"], limit=10
    )
    assert len(rows) == 1


def test_exact_search_does_not_call_the_backend_without_identifiers() -> None:
    runner = FakeRunner()
    assert _backend(runner).exact_search(
        repo=REPO, identifiers=["", "  "], limit=10
    ) == []
    assert runner.calls == []


def test_fulltext_uses_the_decision_index_and_parameterized_query() -> None:
    runner = FakeRunner(fulltext=[{"node": DECISION, "score": 0.9}])
    rows = _backend(runner).fulltext_search(
        repo=REPO, query='"retries" OR "disabled"', limit=20
    )
    query, parameters = runner.only_call()
    assert query == FULLTEXT_QUERY
    assert "decision_fulltext" in query
    assert parameters == {
        "repo": REPO,
        "query": '"retries" OR "disabled"',
        "limit": 20,
    }
    assert [row.decision_id for row in rows] == ["dec_001"]


def test_fulltext_accepts_a_bare_node_record() -> None:
    runner = FakeRunner(fulltext=[DECISION])
    rows = _backend(runner).fulltext_search(
        repo=REPO, query="retries", limit=20
    )
    assert [row.decision_id for row in rows] == ["dec_001"]


def test_vector_query_overfetches_then_applies_the_repo_filter() -> None:
    runner = FakeRunner(vector=[{"node": DECISION, "score": 0.8}])
    rows = _backend(runner).vector_search(
        repo=REPO, embedding=[0.1, 0.2, 0.3], limit=20
    )
    query, parameters = runner.only_call()
    assert query == VECTOR_QUERY
    assert "decision_embedding" in query
    assert parameters == {
        "repo": REPO,
        "embedding": [0.1, 0.2, 0.3],
        "candidates": 80,
        "limit": 20,
    }
    assert [row.decision_id for row in rows] == ["dec_001"]


@pytest.mark.parametrize(
    "embedding",
    [
        [],
        [0.1, 0.2],
        [0.1, 0.2, 0.3, 0.4],
        [0.1, math.nan, 0.3],
        [0.1, math.inf, 0.3],
    ],
)
def test_vector_search_fails_closed_before_querying(embedding: list[float]) -> None:
    runner = FakeRunner(vector=[{"node": DECISION, "score": 0.8}])
    with pytest.raises(InvalidRecordError):
        _backend(runner).vector_search(
            repo=REPO, embedding=embedding, limit=20
        )
    assert runner.calls == []


def test_expand_maps_evidence_targets_pull_requests_incidents_and_history() -> None:
    runner = FakeRunner(
        expand=[
            {
                "d": DECISION,
                "evidence": [EVIDENCE],
                "targets": [
                    {
                        "node": {
                            "record_id": "httpx:symbol:path@sha:Client.send",
                            "qualified_name": "httpx._client.Client.send",
                        },
                        "labels": ["Symbol"],
                    },
                    {
                        "node": {
                            "record_id": "httpx:config:path:retry.max_attempts",
                            "key": "retry.max_attempts",
                        },
                        "labels": ["ConfigKey"],
                    },
                    {
                        "node": {
                            "record_id": "httpx:region:path@sha:310:325",
                            "path": "httpx/_client.py",
                            "start_line": 310,
                            "end_line": 325,
                        },
                        "labels": ["CodeRegion"],
                    },
                ],
                "pull_requests": [{"record_id": "httpx:pr:1234"}],
                "incidents": [{"record_id": "httpx:issue:7"}],
                "later_decisions": [
                    {"node": {"decision_id": "dec_002"}, "relation": "SUPERSEDES"}
                ],
                "conflicts": [
                    {"node": {"decision_id": "dec_003"}, "relation": "CONFLICTS_WITH"}
                ],
            }
        ]
    )
    context = _backend(runner).expand(repo=REPO, decision_id="dec_001")
    assert context is not None
    assert context.decision.decision_id == "dec_001"
    assert context.decision.target_ids == [
        "CodeRegion:httpx/_client.py:310-325",
        "ConfigKey:retry.max_attempts",
        "Symbol:httpx._client.Client.send",
    ]
    assert context.targets == context.decision.target_ids
    assert [item.evidence_id for item in context.evidence] == ["ev_101"]
    assert context.evidence[0].kind == "review_comment"
    assert context.evidence[0].pr_number == 1234
    assert context.evidence[0].author == "alice"
    assert context.evidence[0].created_at == "2024-03-30T09:05:00Z"
    assert context.evidence[0].path == "httpx/_client.py"
    assert context.evidence[0].start_line == 312
    assert context.evidence[0].end_line == 318
    assert context.pull_requests == ["httpx:pr:1234"]
    assert context.incidents == ["httpx:issue:7"]
    assert context.later_decisions == ["dec_002"]
    assert context.conflicts == ["dec_003"]
    assert context.decision.evidence_ids == ["ev_101"]
    assert context.path()[-1] == "Evidence:ev_101"
    assert "Decision:dec_001" in context.path()


def test_expand_infers_a_target_label_when_the_row_has_none() -> None:
    runner = FakeRunner(
        expand=[
            {
                "d": DECISION,
                "evidence": [],
                "targets": [
                    {
                        "record_id": "httpx:config:path:timeout",
                        "key": "timeout",
                    }
                ],
                "pull_requests": [],
                "incidents": [],
                "later_decisions": [],
                "conflicts": [],
            }
        ]
    )
    context = _backend(runner).expand(repo=REPO, decision_id="dec_001")
    assert context is not None
    assert context.targets == ["ConfigKey:timeout"]


def test_expand_returns_none_when_the_decision_is_missing() -> None:
    runner = FakeRunner(expand=[])
    assert _backend(runner).expand(repo=REPO, decision_id="dec_missing") is None


def test_expand_query_uses_the_real_supersession_edge_types() -> None:
    runner = FakeRunner(expand=[])
    _backend(runner).expand(repo=REPO, decision_id="dec_001")
    query, parameters = runner.only_call()
    assert query == EXPAND_QUERY
    assert parameters == {"repo": REPO, "decision_id": "dec_001"}
    for rel_type in ("SUPERSEDES", "REVERSES", "REVISES", "CONFLICTS_WITH"):
        assert rel_type in query
    assert "SUPPORTED_BY" in query
    assert "GOVERNS" in query
    assert "PRODUCED_IN" in query
    assert "MOTIVATED_BY" in query


def test_get_evidence_maps_the_lookup_record() -> None:
    runner = FakeRunner(evidence=[{"e": EVIDENCE}])
    item = _backend(runner).get_evidence(repo=REPO, evidence_id="ev_101")
    assert item is not None
    assert item.evidence_id == "ev_101"
    assert item.kind == "review_comment"
    assert item.pr_number == 1234
    assert item.author == "alice"
    assert item.path == "httpx/_client.py"
    assert item.url == EVIDENCE["url"]
    query, parameters = runner.calls[0]
    assert "MATCH (e:Evidence {repo: $repo})" in query
    assert parameters == {"repo": REPO, "evidence_id": "ev_101"}


def test_evidence_mapping_infers_commit_sha_and_issue_number_from_urls() -> None:
    commit_runner = FakeRunner(
        evidence=[
            {
                "e": {
                    "evidence_id": "ev_commit",
                    "record_type": "commit",
                    "url": "https://github.com/encode/httpx/commit/abc123d",
                }
            }
        ]
    )
    issue_runner = FakeRunner(
        evidence=[
            {
                "e": {
                    "evidence_id": "ev_issue",
                    "record_type": "issue_comment",
                    "url": "https://github.com/encode/httpx/issues/108#issuecomment-1",
                }
            }
        ]
    )
    commit = _backend(commit_runner).get_evidence(
        repo=REPO, evidence_id="ev_commit"
    )
    issue = _backend(issue_runner).get_evidence(
        repo=REPO, evidence_id="ev_issue"
    )
    assert commit is not None and commit.commit_sha == "abc123d"
    assert issue is not None and issue.issue_number == 108


def test_entity_history_expands_each_matching_decision() -> None:
    runner = FakeRunner(
        history=[{"d": DECISION}],
        expand=[
            {
                "d": DECISION,
                "evidence": [EVIDENCE],
                "targets": [],
                "pull_requests": [],
                "incidents": [],
                "later_decisions": [],
                "conflicts": [],
            }
        ],
    )
    contexts = _backend(runner).entity_history(
        repo=REPO, entity_id="retry.max_attempts"
    )
    assert [context.decision.decision_id for context in contexts] == ["dec_001"]
    assert runner.calls[0][1]["entity_id"] == "retry.max_attempts"
    assert runner.calls[1][0] == EXPAND_QUERY


def test_backend_can_supply_its_own_graph_rank() -> None:
    runner = FakeRunner(
        expand=[
            {
                "d": DECISION,
                "evidence": [],
                "targets": [],
                "pull_requests": [],
                "incidents": [],
                "later_decisions": [],
                "conflicts": [],
            }
        ]
    )
    context = _backend(runner).expand(repo=REPO, decision_id="dec_001")
    assert context is not None
    # Search adapters record their own ranks; graph expansion must not
    # pretend the decision came from exact/full-text/vector search.
    assert context.decision.sources == {}
    assert RetrievalSource.GRAPH.value == "graph"


def test_the_adapter_composes_with_the_retrieval_service() -> None:
    runner = FakeRunner(
        exact=[{"d": DECISION, "priority": 1}],
        fulltext=[{"node": DECISION, "score": 0.9}],
        vector=[{"node": DECISION, "score": 0.8}],
        expand=[
            {
                "d": DECISION,
                "evidence": [EVIDENCE],
                "targets": [],
                "pull_requests": [],
                "incidents": [],
                "later_decisions": [],
                "conflicts": [],
            }
        ],
    )
    config = RetrievalConfig(
        repo=REPO, embedding_dimensions=3, parallel_retrieval=False
    )
    backend = Neo4jGraphBackend(runner, config=config)
    service = RetrievalService(
        backend=backend,
        embedder=FakeEmbedder([0.1, 0.2, 0.3]),
        generator=FakeGenerator({ANSWER_SCHEMA_NAME: answer_json()}),
        config=config,
        index=EntityIndex(
            entities=[
                KnownEntity(
                    type=EntityType.CONFIG_KEY,
                    identifier="retry.max_attempts",
                    aliases=("retries", "retry"),
                )
            ]
        ),
        lookup=backend,
    )
    response = service.ask(
        AskParameters(
            question="Why are retries disabled by default in httpx?",
            repo=REPO,
        )
    )
    assert response.answer_status is AnswerStatus.ANSWERED
    assert response.decision_ids == ["dec_001"]
    assert [item.evidence_id for item in response.evidence] == ["ev_101"]
    assert response.retrieval_trace.exact_matches == 1
    assert response.retrieval_trace.graph_candidates == 1
    assert [name for name, _ in runner.calls] == [
        EXACT_QUERY,
        FULLTEXT_QUERY,
        VECTOR_QUERY,
        EXPAND_QUERY,
    ]
