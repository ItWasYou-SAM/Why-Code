"""The prepared demo corpus.

This is fixture data, not a second source of truth. It mirrors the shape the
intent and structural writers put in Neo4j so the retrieval pipeline can run
offline and rerun byte-identically. Every fact in a demo answer is backed by
one of these evidence records.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.models import (
    AnswerConfidence,
    AnswerStatus,
    DecisionCandidate,
    DecisionState,
    EvidenceItem,
    GraphContext,
    RefusalMode,
)
from app.retrieval.question.entity_extractor import EntityIndex, KnownEntity
from app.retrieval.question.models import EntityType

DEMO_REPO = "encode/httpx"

# Decisions are deliberately ordered current-first. The in-memory backend uses
# this order as the tie-break for identifiers that govern more than one node.
DEFAULT_OFF = "dec_httpx_retry_default_off"
CONNECTION_ONLY = "dec_httpx_retry_connection_only"
DEFAULT_ON = "dec_httpx_retry_default_on"
TIMEOUT_PHASES = "dec_httpx_timeout_phases"

QUESTION_1 = "Why are retries disabled by default in httpx?"
QUESTION_2 = "Why are simple retries limited to connection failures?"
QUESTION_3 = "Was the retry approach ever revised or reversed?"
QUESTION_4 = "Why does the client use separate connect, read, write, and pool timeouts?"
QUESTION_5 = "Why does httpx use a purple logo?"


@dataclass(frozen=True)
class DemoQuestionSpec:
    question_id: str
    question: str
    focus: str
    expectation: str
    expected_status: AnswerStatus
    expected_decision_id: str | None = None
    expected_refusal_mode: RefusalMode | None = None
    expects_historical: bool = False


DEMO_SCRIPT: tuple[DemoQuestionSpec, ...] = (
    DemoQuestionSpec(
        question_id="q1",
        question=QUESTION_1,
        focus="Decision, evidence, people and graph path",
        expectation=(
            "A cited answer explains why retries are opt-in and shows the "
            "config key, the decision, the people and the supporting evidence."
        ),
        expected_status=AnswerStatus.ANSWERED,
        expected_decision_id=DEFAULT_OFF,
    ),
    DemoQuestionSpec(
        question_id="q2",
        question=QUESTION_2,
        focus="HTTP idempotency constraint and code-level rationale",
        expectation=(
            "The answer ties the connection-only rule to idempotency and "
            "points at the retry guard in the client code."
        ),
        expected_status=AnswerStatus.ANSWERED,
        expected_decision_id=CONNECTION_ONLY,
    ),
    DemoQuestionSpec(
        question_id="q3",
        question=QUESTION_3,
        focus="Supersession detection",
        expectation=(
            "The historical default is labelled historical, and the current "
            "decision is presented as current truth."
        ),
        expected_status=AnswerStatus.ANSWERED,
        expected_decision_id=DEFAULT_OFF,
        expects_historical=True,
    ),
    DemoQuestionSpec(
        question_id="q4",
        question=QUESTION_4,
        focus="A different subsystem and config-key path",
        expectation=(
            "The answer explains why the timeout phases are separate and "
            "follows a different config key through the graph."
        ),
        expected_status=AnswerStatus.ANSWERED,
        expected_decision_id=TIMEOUT_PHASES,
    ),
    DemoQuestionSpec(
        question_id="q5",
        question=QUESTION_5,
        focus="Evidence-bounded refusal",
        expectation=(
            "The system refuses because no code entity or decision trail "
            "supports a question about the logo."
        ),
        expected_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
        expected_refusal_mode=RefusalMode.NO_ENTITY,
    ),
)

SCRIPT_BY_QUESTION = {spec.question: spec for spec in DEMO_SCRIPT}


def build_entity_index() -> EntityIndex:
    """The allowlist the deterministic entity extractor may resolve.

    Aliases are chosen so the prepared questions resolve without a model:
    Q1 matches the full phrase "retries disabled by default", Q4 matches
    "timeouts", and Q5 matches nothing at all.
    """

    entities = [
        KnownEntity(
            type=EntityType.CONFIG_KEY,
            identifier="retry.max_attempts",
            aliases=(
                "retries disabled by default",
                "retry defaults",
                "retry",
            ),
            display_name="retry.max_attempts",
            path="httpx/_config.py",
        ),
        KnownEntity(
            type=EntityType.CONFIG_KEY,
            identifier="timeout",
            aliases=("timeouts", "pool timeout"),
            display_name="timeout",
            path="httpx/_config.py",
        ),
        KnownEntity(
            type=EntityType.SYMBOL,
            identifier="httpx._client.Client._send_handling_redirects",
            aliases=(
                "Client._send_handling_redirects",
                "send_handling_redirects",
            ),
            display_name="_send_handling_redirects",
            path="httpx/_client.py",
        ),
        KnownEntity(
            type=EntityType.SYMBOL,
            identifier="httpx._config.Timeout",
            aliases=("Timeout", "timeouts"),
            display_name="Timeout",
            path="httpx/_config.py",
        ),
        KnownEntity(
            type=EntityType.FILE,
            identifier="httpx/_client.py",
            aliases=("_client.py",),
            path="httpx/_client.py",
        ),
        KnownEntity(
            type=EntityType.FILE,
            identifier="httpx/_config.py",
            aliases=("_config.py",),
            path="httpx/_config.py",
        ),
        KnownEntity(
            type=EntityType.DECISION,
            identifier=DEFAULT_OFF,
            claim="Retries are disabled by default; callers must opt in explicitly.",
            status="active",
            pr_number=1525,
        ),
        KnownEntity(
            type=EntityType.DECISION,
            identifier=CONNECTION_ONLY,
            claim=(
                "Simple retries are limited to connection failures because "
                "only connection errors are safe to retry."
            ),
            status="active",
            pr_number=1419,
        ),
        KnownEntity(
            type=EntityType.DECISION,
            identifier=DEFAULT_ON,
            claim=(
                "The client originally enabled automatic retries for "
                "transient network errors."
            ),
            status="reversed",
            pr_number=1200,
        ),
        KnownEntity(
            type=EntityType.DECISION,
            identifier=TIMEOUT_PHASES,
            claim=(
                "The client uses separate connect, read, write, and pool "
                "timeouts because each phase can fail independently."
            ),
            status="active",
            pr_number=1215,
        ),
    ]
    return EntityIndex(entities=entities)


def build_contexts() -> dict[str, GraphContext]:
    return {
        DEFAULT_OFF: _default_off_context(),
        CONNECTION_ONLY: _connection_only_context(),
        TIMEOUT_PHASES: _timeout_context(),
        DEFAULT_ON: _default_on_context(),
    }


def _default_off_context() -> GraphContext:
    evidence = [
        _evidence(
            "httpx:review_comment:300",
            kind="review_comment",
            excerpt=(
                "Retries should be opt-in. Turning them on by default changed "
                "behaviour for every client without warning."
            ),
            author="tomchristie",
            created_at="2024-06-03T10:12:00Z",
            pr_number=1525,
            path="httpx/_config.py",
            start_line=255,
            end_line=270,
        ),
        _evidence(
            "httpx:commit:cccccccccccccccccccccccccccccccccccccccc",
            kind="commit",
            excerpt=(
                "Revert automatic retries and default the retry count to zero."
            ),
            author="tomchristie",
            created_at="2024-06-04T08:30:00Z",
            pr_number=1525,
            commit_sha="cccccccccccccccccccccccccccccccccccccccc",
        ),
        _evidence(
            "httpx:pr:1525:file:httpx/_config.py",
            kind="changed_file",
            excerpt=(
                "@@ -258,6 +258,6 @@ class Limits:\n"
                "-    retries: int = 3\n"
                "+    retries: int = 0"
            ),
            author="tomchristie",
            created_at="2024-06-04T08:35:00Z",
            pr_number=1525,
            path="httpx/_config.py",
            start_line=255,
            end_line=270,
        ),
        _evidence(
            "httpx:issue_comment:9100",
            kind="issue_comment",
            excerpt=(
                "Turning retries on by default broke clients that send "
                "non-idempotent requests."
            ),
            author="lukasa",
            created_at="2024-05-30T16:20:00Z",
            issue_number=9100,
            path=None,
        ),
        _evidence(
            "httpx:doc:retries-default",
            kind="doc_file",
            excerpt=(
                "Retries are disabled by default; enable them explicitly on "
                "the transport."
            ),
            author="tomchristie",
            created_at="2024-06-05T09:00:00Z",
            path="docs/advanced/clients.md",
        ),
    ]
    decision = DecisionCandidate(
        decision_id=DEFAULT_OFF,
        claim=(
            "Retries are disabled by default; callers must opt in explicitly."
        ),
        rationale=(
            "This approach revised the earlier retry default: automatic "
            "retries can duplicate non-idempotent requests and amplify an "
            "outage, so retrying is not safe by default. PR #1525 reset the "
            "retry.max_attempts default to zero after the earlier behaviour "
            "caused regressions."
        ),
        status=DecisionState.ACTIVE,
        confidence=AnswerConfidence.HIGH,
        target_ids=[
            "ConfigKey:retry.max_attempts",
            "Symbol:httpx._client.Client._send_handling_redirects",
            "CodeRegion:httpx/_config.py:255-270",
            "File:httpx/_config.py",
            "Commit:cccccccccccccccccccccccccccccccccccccccc",
        ],
        evidence_ids=[item.evidence_id for item in evidence],
        created_at="2024-06-03T10:12:00Z",
    )
    return GraphContext(
        decision=decision,
        evidence=evidence,
        targets=list(decision.target_ids),
        pull_requests=["PullRequest:#1525"],
        later_decisions=[],
        conflicts=[],
    )


def _connection_only_context() -> GraphContext:
    evidence = [
        _evidence(
            "httpx:review_comment:123",
            kind="review_comment",
            excerpt=(
                "We cannot retry every failure: only connection errors are "
                "safe, because the request may already have reached the server."
            ),
            author="tomchristie",
            created_at="2024-03-14T11:05:00Z",
            pr_number=1419,
            path="httpx/_client.py",
            start_line=1394,
            end_line=1412,
        ),
        _evidence(
            "httpx:commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            kind="commit",
            excerpt=(
                "Only retry connection failures; do not retry read, write, "
                "or pool timeouts."
            ),
            author="tomchristie",
            created_at="2024-03-15T07:45:00Z",
            pr_number=1419,
            commit_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
        _evidence(
            "httpx:pr:1419:file:httpx/_client.py",
            kind="changed_file",
            excerpt=(
                "@@ -1398,7 +1398,9 @@ def _send_handling_redirects(\n"
                "-        if self._transport.retries:\n"
                "+        if isinstance(exc, ConnectError) and self._transport.retries:"
            ),
            author="tomchristie",
            created_at="2024-03-15T07:50:00Z",
            pr_number=1419,
            path="httpx/_client.py",
            start_line=1394,
            end_line=1412,
        ),
        _evidence(
            "httpx:issue_comment:9001",
            kind="issue_comment",
            excerpt=(
                "A retry after a write failure can duplicate a POST that the "
                "server already accepted."
            ),
            author="lukasa",
            created_at="2024-03-10T18:40:00Z",
            issue_number=9001,
        ),
        _evidence(
            "httpx:doc:retries-connection",
            kind="doc_file",
            excerpt="Only connection failures are retried automatically.",
            author="tomchristie",
            created_at="2024-03-16T10:00:00Z",
            path="docs/advanced/clients.md",
        ),
    ]
    decision = DecisionCandidate(
        decision_id=CONNECTION_ONLY,
        claim=(
            "Simple retries are limited to connection failures because only "
            "connection errors are safe to retry."
        ),
        rationale=(
            "HTTP methods are not guaranteed to be idempotent. A retry after "
            "a read or write failure could duplicate an operation, so the "
            "retry guard only resends when the connection itself failed. This "
            "revised the retry approach without changing the opt-in default."
        ),
        status=DecisionState.ACTIVE,
        confidence=AnswerConfidence.HIGH,
        target_ids=[
            "Symbol:httpx._client.Client._send_handling_redirects",
            "CodeRegion:httpx/_client.py:1394-1412",
            "File:httpx/_client.py",
            "Commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ],
        evidence_ids=[item.evidence_id for item in evidence],
        created_at="2024-03-14T11:05:00Z",
    )
    return GraphContext(
        decision=decision,
        evidence=evidence,
        targets=list(decision.target_ids),
        pull_requests=["PullRequest:#1419"],
        later_decisions=[],
        conflicts=[],
    )


def _default_on_context() -> GraphContext:
    evidence = [
        _evidence(
            "httpx:review_comment:120",
            kind="review_comment",
            excerpt=(
                "Let's retry transient failures by default so callers do not "
                "have to wrap every request."
            ),
            author="tomchristie",
            created_at="2023-10-02T09:20:00Z",
            pr_number=1200,
            path="httpx/_config.py",
            start_line=250,
            end_line=262,
        ),
        _evidence(
            "httpx:commit:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            kind="commit",
            excerpt="Enable retries for transient failures by default.",
            author="tomchristie",
            created_at="2023-10-03T12:10:00Z",
            pr_number=1200,
            commit_sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ),
        _evidence(
            "httpx:pr:1200:file:httpx/_config.py",
            kind="changed_file",
            excerpt=(
                "@@ -254,5 +254,5 @@ class Limits:\n"
                "-    retries: int = 0\n"
                "+    retries: int = 3"
            ),
            author="tomchristie",
            created_at="2023-10-03T12:15:00Z",
            pr_number=1200,
            path="httpx/_config.py",
            start_line=250,
            end_line=262,
        ),
        _evidence(
            "httpx:issue_comment:7000",
            kind="issue_comment",
            excerpt=(
                "Should the client retry transient failures by default?"
            ),
            author="lukasa",
            created_at="2023-09-28T15:00:00Z",
            issue_number=7000,
        ),
    ]
    decision = DecisionCandidate(
        decision_id=DEFAULT_ON,
        claim=(
            "The client originally enabled automatic retries for transient "
            "network errors."
        ),
        rationale=(
            "This retry approach was later revised and then reversed: the "
            "default was convenient for transient failures but unsafe for "
            "non-idempotent calls. The current default is to opt in."
        ),
        status=DecisionState.REVERSED,
        confidence=AnswerConfidence.MEDIUM,
        target_ids=[
            "ConfigKey:retry.enabled",
            "File:httpx/_config.py",
            "Commit:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ],
        evidence_ids=[item.evidence_id for item in evidence],
        superseded_by=DEFAULT_OFF,
        created_at="2023-10-02T09:20:00Z",
    )
    return GraphContext(
        decision=decision,
        evidence=evidence,
        targets=list(decision.target_ids),
        pull_requests=["PullRequest:#1200"],
        later_decisions=[DEFAULT_OFF],
        conflicts=[],
    )


def _timeout_context() -> GraphContext:
    evidence = [
        _evidence(
            "httpx:review_comment:500",
            kind="review_comment",
            excerpt=(
                "A pool timeout is not the same failure as a read timeout; "
                "callers need to distinguish them."
            ),
            author="tomchristie",
            created_at="2023-11-10T13:30:00Z",
            pr_number=1215,
            path="httpx/_config.py",
            start_line=120,
            end_line=150,
        ),
        _evidence(
            "httpx:commit:dddddddddddddddddddddddddddddddddddddddd",
            kind="commit",
            excerpt=(
                "Split Timeout into connect, read, write, and pool phases."
            ),
            author="tomchristie",
            created_at="2023-11-11T08:15:00Z",
            pr_number=1215,
            commit_sha="dddddddddddddddddddddddddddddddddddddddd",
        ),
        _evidence(
            "httpx:pr:1215:file:httpx/_config.py",
            kind="changed_file",
            excerpt=(
                "@@ -118,7 +118,13 @@ class Timeout:\n"
                "+    connect: float | None\n"
                "+    read: float | None\n"
                "+    write: float | None\n"
                "+    pool: float | None"
            ),
            author="tomchristie",
            created_at="2023-11-11T08:20:00Z",
            pr_number=1215,
            path="httpx/_config.py",
            start_line=120,
            end_line=160,
        ),
        _evidence(
            "httpx:issue_comment:8000",
            kind="issue_comment",
            excerpt=(
                "A single timeout makes it impossible to tell connection "
                "starvation from a slow server."
            ),
            author="lukasa",
            created_at="2023-11-08T17:05:00Z",
            issue_number=8000,
        ),
        _evidence(
            "httpx:doc:timeouts",
            kind="doc_file",
            excerpt=(
                "Timeouts can be set separately for connect, read, write, "
                "and pool."
            ),
            author="tomchristie",
            created_at="2023-11-12T09:40:00Z",
            path="docs/advanced/timeouts.md",
        ),
    ]
    decision = DecisionCandidate(
        decision_id=TIMEOUT_PHASES,
        claim=(
            "The client uses separate connect, read, write, and pool "
            "timeouts because each phase can fail independently."
        ),
        rationale=(
            "The client uses one timeout per phase so callers can tell a "
            "connection delay from a slow server or an exhausted pool without "
            "guessing which subsystem stalled."
        ),
        status=DecisionState.ACTIVE,
        confidence=AnswerConfidence.HIGH,
        target_ids=[
            "ConfigKey:timeout",
            "Symbol:httpx._config.Timeout",
            "CodeRegion:httpx/_config.py:120-160",
            "File:httpx/_config.py",
            "Commit:dddddddddddddddddddddddddddddddddddddddd",
        ],
        evidence_ids=[item.evidence_id for item in evidence],
        created_at="2023-11-10T13:30:00Z",
    )
    return GraphContext(
        decision=decision,
        evidence=evidence,
        targets=list(decision.target_ids),
        pull_requests=["PullRequest:#1215"],
        later_decisions=[],
        conflicts=[],
    )


def _evidence(
    evidence_id: str,
    *,
    kind: str,
    excerpt: str,
    author: str,
    created_at: str,
    pr_number: int | None = None,
    issue_number: int | None = None,
    commit_sha: str | None = None,
    path: str | None = None,
    start_line: int | None = None,
    end_line: int | None = None,
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        kind=kind,
        excerpt=excerpt,
        url=_url(
            pr_number=pr_number,
            issue_number=issue_number,
            commit_sha=commit_sha,
        ),
        decision_id=_decision_for(evidence_id),
        pr_number=pr_number,
        issue_number=issue_number,
        commit_sha=commit_sha,
        author=author,
        created_at=created_at,
        path=path,
        start_line=start_line,
        end_line=end_line,
    )


def _decision_for(evidence_id: str) -> str:
    if (
        ":1525:" in evidence_id
        or evidence_id.endswith((":300", ":9100"))
        or "commit:cccc" in evidence_id
        or "retries-default" in evidence_id
    ):
        return DEFAULT_OFF
    if (
        ":1419:" in evidence_id
        or evidence_id.endswith((":123", ":9001"))
        or "commit:aaaa" in evidence_id
        or "retries-connection" in evidence_id
    ):
        return CONNECTION_ONLY
    if (
        ":1200:" in evidence_id
        or evidence_id.endswith((":120", ":7000"))
        or "commit:bbbb" in evidence_id
    ):
        return DEFAULT_ON
    return TIMEOUT_PHASES


def _url(
    *,
    pr_number: int | None,
    issue_number: int | None,
    commit_sha: str | None,
) -> str | None:
    if pr_number is not None:
        return f"https://github.com/encode/httpx/pull/{pr_number}"
    if issue_number is not None:
        return f"https://github.com/encode/httpx/issues/{issue_number}"
    if commit_sha is not None:
        return f"https://github.com/encode/httpx/commit/{commit_sha}"
    return None


__all__ = [
    "CONNECTION_ONLY",
    "DEFAULT_OFF",
    "DEFAULT_ON",
    "DEMO_REPO",
    "DEMO_SCRIPT",
    "QUESTION_1",
    "QUESTION_2",
    "QUESTION_3",
    "QUESTION_4",
    "QUESTION_5",
    "SCRIPT_BY_QUESTION",
    "TIMEOUT_PHASES",
    "DemoQuestionSpec",
    "build_contexts",
    "build_entity_index",
]
