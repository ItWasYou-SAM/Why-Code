"""End-to-end intent pipeline over the fixture corpora (Phase 3 schema).

Two merged PRs are run through detection, packing, one unified structured
extraction per PR, evidence validation, target resolution, rule enforcement,
confidence scoring, de-duplication and supersession with a deterministic
``StaticProvider``. The tests pin the replay identifier scheme, the counter
arithmetic, the evidence gate, the extraction-rule gate and the cross-PR
reversal behaviour that the unit tests exercise one layer at a time.
"""

from __future__ import annotations

import json
import re
from typing import Any

from intent_helpers import REPO, records, static_client, structural_records

from app.domain.records import EvidenceRecord
from app.intent.config import IntentConfig
from app.intent.llm.schemas import SupersessionVerdict
from app.intent.models import DecisionStatus, ReversalIntent, ReviewReason
from app.intent.pipeline import IntentPipeline
from app.intent.selection.evidence_selector import EvidenceSelector
from app.intent.validation.evidence_validator import FinalStatus
from app.intent.validation.graph_link_validator import GraphIndex
from app.intent.validation.supersession import SupersessionCandidate

RETRY_ISSUE_COMMENT = "httpx:issue_comment:9001"
RETRY_REVIEW = "httpx:review:2001"
RETRY_COMMIT = "httpx:commit:" + "a" * 40
RETRY_DIFF = "httpx:pr:1419:file:httpx/_client.py"
RETRY_COMMENT = "httpx:review_comment:123"

TIMEOUT_ISSUE_COMMENT = "httpx:issue_comment:9100"
TIMEOUT_REVIEW = "httpx:review:2100"
TIMEOUT_COMMIT = "httpx:commit:" + "c" * 40
TIMEOUT_DIFF = "httpx:pr:1525:file:httpx/_config.py"
TIMEOUT_COMMENT = "httpx:review_comment:300"


def candidate(
    claim: str,
    evidence_ids: list[str],
    *,
    decision_type: str = "design_decision",
    status: str = "accepted",
    rationale: str = "",
    config_target: bool = True,
    valid_from: str = "2024-04-02T06:30:45Z",
) -> dict[str, Any]:
    targets = (
        [{"target_type": "config_key", "target_id": "retries"}]
        if config_target
        else []
    )
    return {
        "claim": claim,
        "decision_type": decision_type,
        "status": status,
        "targets": targets,
        "evidence_ids": evidence_ids,
        "participants": [],
        "rationale": rationale,
        "temporal_scope": {"valid_from": valid_from, "valid_until": None},
    }


RETRY_DECISION = candidate(
    "Retries are limited to connection errors.",
    [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF],
    rationale="Retrying a read or write can duplicate server-side effects.",
)
RETRY_CONSTRAINT = candidate(
    "The client must never retry a non-connection request.",
    [RETRY_REVIEW, RETRY_ISSUE_COMMENT],
    decision_type="constraint",
    rationale="Only connect failures are safe to repeat.",
)
TIMEOUT_DECISION = candidate(
    "Retries are disabled by default and no longer enabled.",
    [TIMEOUT_COMMENT, TIMEOUT_COMMIT, TIMEOUT_DIFF],
    rationale="The default duplicated writes and caused incidents.",
    valid_from="2024-06-01T08:00:00Z",
)
TIMEOUT_CONSTRAINT = candidate(
    "Retries must be opted into explicitly by the caller.",
    [TIMEOUT_REVIEW, TIMEOUT_ISSUE_COMMENT],
    decision_type="constraint",
    rationale="Defaults must not silently repeat requests.",
    valid_from="2024-06-01T08:00:00Z",
)
TIMEOUT_REVERSAL = candidate(
    "Revert the default retry behaviour.",
    [TIMEOUT_COMMENT, TIMEOUT_COMMIT],
    decision_type="reversal",
    status="reversed",
    rationale="The previous decision proved unsafe in production.",
    valid_from="2024-06-01T08:00:00Z",
)


def envelope(*candidates: dict[str, Any]) -> str:
    return json.dumps(
        {"candidates": list(candidates), "missing_evidence": []}
    )


def responses() -> dict[str, str | list[str]]:
    return {
        "decision_extraction": [
            envelope(RETRY_DECISION, RETRY_CONSTRAINT),
            envelope(TIMEOUT_DECISION, TIMEOUT_CONSTRAINT, TIMEOUT_REVERSAL),
        ],
    }


class StubSupersessionJudge:
    """Deterministic stand-in for the LLM judge in pipeline tests."""

    def judge(self, candidate: SupersessionCandidate) -> SupersessionVerdict:
        if isinstance(candidate.later, ReversalIntent) or (
            "no longer" in candidate.later.claim
        ):
            relationship = "reverses"
        elif candidate.signals:
            relationship = "supersedes"
        else:
            relationship = "unrelated"
        return SupersessionVerdict(
            earlier_decision_id=candidate.earlier.id,
            later_decision_id=candidate.later.id,
            relationship=relationship,
            evidence_ids=[candidate.earlier.evidence_ids[0]],
            explanation="pipeline test verdict",
        )


def build_pipeline(**overrides: Any) -> tuple[IntentPipeline, dict[str, Any]]:
    fixture = [*records("retry_pr_evidence.json"), *records("timeout_pr_evidence.json")]
    from intent_helpers import FakeStore

    index = GraphIndex.from_sources(
        repo=REPO,
        normalized_records=fixture,
        structural_records=structural_records(),
    )
    config = IntentConfig(repo=REPO, **overrides)
    pipeline = IntentPipeline(
        config=config,
        store=FakeStore(fixture),
        index=index,
        structured_client=static_client(responses()),
        supersession_judge=StubSupersessionJudge(),
    )
    return pipeline, _bundles(fixture)


def _bundles(fixture: list[Any]):
    return EvidenceSelector(REPO).select(fixture)


def run(**overrides: Any):
    pipeline, bundles = build_pipeline(**overrides)
    return pipeline.run(bundles)


class TestRunCounters:
    def test_all_five_intents_are_accepted(self) -> None:
        result = run()
        kinds = sorted(intent.kind.value for intent in result.intents)
        assert kinds == ["constraint", "constraint", "decision", "decision", "reversal"]

    def test_summary_counts_every_stage(self) -> None:
        result = run()
        summary = result.summary
        assert summary.prs_scanned == 2
        assert summary.candidates["DECISION_CANDIDATE"] == 2
        assert summary.candidates["CONSTRAINT_CANDIDATE"] == 2
        assert summary.candidates["REVERSAL_CANDIDATE"] == 1
        assert summary.candidates["INCIDENT_CANDIDATE"] == 2
        assert summary.accepted == {"decision": 2, "constraint": 2, "reversal": 1}
        assert summary.proposals == 5
        assert summary.review_items == 0
        assert summary.rejected == 0

    def test_every_intent_cites_existing_evidence(self) -> None:
        result = run()
        assert result.evidence_refs
        known = {ref.evidence_id for ref in result.evidence_refs}
        for intent in result.intents:
            assert intent.evidence_ids
            assert set(intent.evidence_ids) <= known
            assert intent.confidence.value in {
                "high",
                "medium",
                "low",
                "insufficient",
            }
            assert intent.decision_type.value

    def test_confidence_is_medium_with_discussion_commit_and_diff(self) -> None:
        result = run()
        retry = next(
            intent for intent in result.intents if intent.pr_number == 1419
            and intent.kind.value == "decision"
        )
        assert retry.confidence.value == "medium"

    def test_extraction_classification_is_preserved(self) -> None:
        result = run()
        retry = next(
            intent for intent in result.intents if intent.pr_number == 1419
            and intent.kind.value == "decision"
        )
        assert retry.decision_type.value == "design_decision"
        assert retry.extraction_status.value == "accepted"
        assert retry.temporal_scope is not None
        assert retry.temporal_scope.valid_from == "2024-04-02T06:30:45Z"

    def test_intents_carry_the_phase8_write_contract(self) -> None:
        result = run()
        retry = next(
            intent for intent in result.intents if intent.pr_number == 1419
            and intent.kind.value == "decision"
        )
        assert retry.confidence_score > 0.0
        assert retry.extracted_by == "gpt-4o-mini"
        assert retry.extraction_version == "1"


class TestPhaseEightWriteContract:
    def test_written_graph_keeps_the_decision_write_contract(self) -> None:
        result = run()
        graph = result.graph
        assert graph is not None
        by_key = {node.key: node for node in graph.nodes}
        decisions = [
            by_key[intent.id]
            for intent in result.intents
            if intent.kind.value == "decision"
        ]
        assert decisions
        for node in decisions:
            assert node.properties["decision_id"] == node.key
            assert node.properties["confidence_label"]
            assert node.properties["confidence_score"] >= 0.0
            assert node.properties["extracted_by"]
            assert node.properties["extraction_version"]
        supported = [
            rel
            for rel in graph.relationships
            if rel.rel_type == "SUPPORTED_BY"
        ]
        assert supported
        for rel in supported:
            assert rel.properties["validator_version"]
            assert rel.properties["support_score"] >= 0.0
        governs = [
            rel
            for rel in graph.relationships
            if rel.rel_type == "GOVERNS"
        ]
        assert governs
        for rel in governs:
            assert rel.properties["linked_by"] == "intent_extractor"
            assert rel.properties["linked_at"]


class TestReplay:
    def test_replaying_the_same_input_yields_the_same_ids(self) -> None:
        first = run()
        second = run()
        assert [intent.id for intent in first.intents] == [
            intent.id for intent in second.intents
        ]


class TestSupersession:
    def test_timeout_reversal_reverses_the_retry_decision(self) -> None:
        result = run()
        by_id = {intent.id: intent for intent in result.intents}
        retry = next(
            intent for intent in result.intents if intent.pr_number == 1419
            and intent.kind.value == "decision"
        )
        reversal = next(
            intent for intent in result.intents if intent.kind.value == "reversal"
        )
        assert retry.status is DecisionStatus.REVERSED
        assert retry.superseded_by == reversal.id
        assert reversal.reverses == [retry.id]
        rel_types = {edge.rel_type for edge in result.supersession_edges}
        assert rel_types == {"REVERSES"}
        assert {
            decision.relationship
            for decision in result.supersession_decisions
        } == {"reverses"}
        assert by_id[retry.id].status is DecisionStatus.REVERSED

    def test_reverses_edge_is_written_once_with_its_reason(self) -> None:
        result = run()
        assert result.graph is not None
        edges = [
            relationship
            for relationship in result.graph.relationships
            if relationship.rel_type == "REVERSES"
        ]
        assert len(edges) == 1
        assert edges[0].properties.get("reason")

    def test_supersession_decisions_are_serialized_with_the_batch(self) -> None:
        data = run().as_dict()
        assert data["supersession_decisions"]
        first = data["supersession_decisions"][0]
        assert set(first) == {
            "earlier_decision_id",
            "later_decision_id",
            "relationship",
            "evidence_ids",
            "explanation",
            "target_keys",
        }
        assert first["relationship"] in {
            "reverses",
            "revises",
            "supersedes",
            "unrelated",
            "insufficient_evidence",
        }


class TestEvidenceGate:
    def test_unknown_evidence_is_queued_and_not_accepted(self) -> None:
        bad = candidate(
            "This decision cites evidence that does not exist.",
            ["httpx:commit:deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"],
        )
        fixture = [
            *records("retry_pr_evidence.json"),
            *records("timeout_pr_evidence.json"),
        ]
        from intent_helpers import FakeStore

        index = GraphIndex.from_sources(
            repo=REPO,
            normalized_records=fixture,
            structural_records=structural_records(),
        )
        from app.intent.llm.provider import StaticProvider
        from app.intent.llm.structured_client import StructuredClient

        provider = StaticProvider(
            {
                "decision_extraction": [
                    envelope(bad),
                    envelope(TIMEOUT_DECISION, TIMEOUT_REVERSAL),
                ],
            }
        )
        pipeline = IntentPipeline(
            config=IntentConfig(repo=REPO),
            store=FakeStore(fixture),
            index=index,
            structured_client=StructuredClient(provider, max_attempts=1),
        )
        result = pipeline.run(EvidenceSelector(REPO).select(fixture))

        assert all(
            "deadbeef" not in intent.id for intent in result.intents
        )
        assert not any(
            intent.claim == bad["claim"] for intent in result.intents
        )
        assert result.review_queue is not None
        reasons = {item.reason for item in result.review_queue.items}
        assert ReviewReason.INVALID_EVIDENCE in reasons
        assert result.summary.rejected >= 1


class TestRuleGate:
    def test_refactor_without_rationale_is_rejected(self) -> None:
        bad = candidate(
            "Refactor the client request path.",
            [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF],
            rationale="",
        )
        fixture = records("retry_pr_evidence.json")
        from intent_helpers import FakeStore

        from app.intent.llm.provider import StaticProvider
        from app.intent.llm.structured_client import StructuredClient

        index = GraphIndex.from_sources(
            repo=REPO,
            normalized_records=fixture,
            structural_records=structural_records(),
        )
        provider = StaticProvider({"decision_extraction": [envelope(bad)]})
        pipeline = IntentPipeline(
            config=IntentConfig(repo=REPO),
            store=FakeStore(fixture),
            index=index,
            structured_client=StructuredClient(provider, max_attempts=1),
        )
        result = pipeline.run(EvidenceSelector(REPO).select(fixture))

        assert not result.intents
        assert result.review_queue is not None
        reasons = {item.reason for item in result.review_queue.items}
        assert ReviewReason.RULE_VIOLATION in reasons


class TestMissingEvidence:
    def test_missing_evidence_is_queued_instead_of_guessed(self) -> None:
        fixture = records("retry_pr_evidence.json")
        from intent_helpers import FakeStore

        from app.intent.llm.provider import StaticProvider
        from app.intent.llm.structured_client import StructuredClient

        provider = StaticProvider(
            {
                "decision_extraction": [
                    json.dumps(
                        {
                            "candidates": [],
                            "missing_evidence": [
                                "no maintainer rationale in the pack"
                            ],
                        }
                    )
                ]
            }
        )
        pipeline = IntentPipeline(
            config=IntentConfig(repo=REPO),
            store=FakeStore(fixture),
            index=GraphIndex.from_sources(
                repo=REPO,
                normalized_records=fixture,
                structural_records=structural_records(),
            ),
            structured_client=StructuredClient(provider, max_attempts=1),
        )
        result = pipeline.run(EvidenceSelector(REPO).select(fixture))

        assert not result.intents
        assert result.review_queue is not None
        item = result.review_queue.items[0]
        assert item.reason is ReviewReason.INSUFFICIENT_EVIDENCE
        assert "no maintainer rationale" in item.issues[0]


class _AliasProvider:
    """Decision answer that cites the [E#] labels its own prompt offered."""

    def __init__(self, alias: str | None = None) -> None:
        self.alias = alias
        self.prompts: list[str] = []
        self.resolved: list[str] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        del system, schema
        self.prompts.append(user)
        if schema_name == "decision_extraction":
            pairs = re.findall(r"\[(E\d+)\] (httpx:[^\s]+)", user)
            if self.alias is not None:
                evidence_ids = [self.alias]
            else:
                evidence_ids = [label for label, _ in pairs[:3]]
                self.resolved = [record for _, record in pairs[:3]]
            return envelope(
                candidate(
                    "Retries are limited to connection errors.",
                    evidence_ids,
                    rationale="Only connect failures are safe to repeat.",
                )
            )
        raise AssertionError(schema_name)


class TestAliasCitations:
    def _run(self, provider: _AliasProvider):
        from intent_helpers import FakeStore

        from app.intent.llm.structured_client import StructuredClient

        fixture = records("retry_pr_evidence.json")
        index = GraphIndex.from_sources(
            repo=REPO,
            normalized_records=fixture,
            structural_records=structural_records(),
        )
        pipeline = IntentPipeline(
            config=IntentConfig(repo=REPO),
            store=FakeStore(fixture),
            index=index,
            structured_client=StructuredClient(provider, max_attempts=1),
        )
        return pipeline.run(EvidenceSelector(REPO).select(fixture))

    def test_e_labels_are_resolved_before_the_evidence_gate(self) -> None:
        provider = _AliasProvider()
        result = self._run(provider)
        assert provider.resolved
        assert len(result.intents) == 1
        intent = result.intents[0]
        assert intent.kind.value == "decision"
        assert set(intent.evidence_ids) == set(provider.resolved)
        assert all(not evidence_id.startswith("E") for evidence_id in intent.evidence_ids)

    def test_unknown_alias_is_rejected_as_outside_the_pack(self) -> None:
        result = self._run(_AliasProvider(alias="E99"))
        assert not result.intents
        assert result.review_queue is not None
        reasons = {item.reason for item in result.review_queue.items}
        assert ReviewReason.INVALID_EVIDENCE in reasons


class TestPhase4Firewall:
    def test_unrelated_claim_is_queued_as_unsupported(self) -> None:
        result = _single(
            candidate(
                "The team adopted GraphQL for all internal services.",
                [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF],
            )
        )
        assert not result.intents
        assert result.review_queue is not None
        reasons = {item.reason for item in result.review_queue.items}
        assert ReviewReason.UNSUPPORTED_CLAIM in reasons
        assert result.evidence_validations[0].final_status is (
            FinalStatus.NEEDS_REVIEW
        )
        assert result.evidence_validations[0].unsupported_claims

    def test_no_structural_target_is_rejected(self) -> None:
        result = _single(
            candidate(
                "Retries are limited to connection errors.",
                [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF],
                config_target=False,
            )
        )
        assert not result.intents
        assert result.review_queue is not None
        reasons = {item.reason for item in result.review_queue.items}
        assert ReviewReason.UNRESOLVED_TARGET in reasons
        assert result.evidence_validations[0].final_status is (
            FinalStatus.REJECTED
        )

    def test_invented_temporal_date_is_sent_to_review(self) -> None:
        result = _single(
            candidate(
                "Retries are limited to connection errors.",
                [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF],
                valid_from="2024-05-05T00:00:00Z",
            )
        )
        assert not result.intents
        assert result.review_queue is not None
        reasons = {item.reason for item in result.review_queue.items}
        assert ReviewReason.INVALID_TEMPORAL in reasons

    def test_partial_targets_become_an_observation(self) -> None:
        payload = candidate(
            "Retries are limited to connection errors.",
            [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF],
        )
        payload["targets"] = [
            {"target_type": "config_key", "target_id": "retries"},
            {"target_type": "symbol", "target_id": "Missing"},
        ]
        result = _single(payload)
        assert len(result.intents) == 1
        assert result.intents[0].status is DecisionStatus.UNCERTAIN
        assert result.review_queue is not None
        reasons = {item.reason for item in result.review_queue.items}
        assert ReviewReason.UNRESOLVED_TARGET in reasons
        assert result.evidence_validations[0].final_status is (
            FinalStatus.OBSERVATION
        )

    def test_conflicting_evidence_marks_the_intent_contested(self) -> None:
        rows = [
            *records("retry_pr_evidence.json"),
            EvidenceRecord.model_validate(
                {
                    "record_id": "httpx:review_comment:999",
                    "record_type": "review_comment",
                    "repo": REPO,
                    "pr_number": 1419,
                    "payload": {
                        "body": "I disagree; we should not retry connection errors.",
                        "author": "skeptic",
                        "path": "httpx/_client.py",
                        "line": 110,
                        "side": "RIGHT",
                        "created_at": "2024-03-30T09:15:00Z",
                    },
                    "content_hash": "synthetic",
                    "ingested_at": "2024-03-30T09:15:00Z",
                }
            ),
        ]
        result = _single(
            candidate(
                "Retries are limited to connection errors.",
                [
                    RETRY_COMMENT,
                    RETRY_COMMIT,
                    RETRY_DIFF,
                    "httpx:review_comment:999",
                ],
            ),
            rows=rows,
        )
        assert len(result.intents) == 1
        assert result.intents[0].status is DecisionStatus.CONTESTED
        assert result.review_queue is not None
        reasons = {item.reason for item in result.review_queue.items}
        assert ReviewReason.CONTESTED in reasons

    def test_validation_results_are_serialized_with_the_batch(self) -> None:
        result = run()
        data = result.as_dict()
        assert data["evidence_validations"]
        first = data["evidence_validations"][0]
        assert set(first) == {
            "decision_id",
            "schema_valid",
            "evidence_valid",
            "targets_valid",
            "unsupported_claims",
            "invalid_evidence_ids",
            "invalid_target_ids",
            "final_status",
        }


def _single(payload: dict[str, Any], *, rows=None) -> Any:
    from intent_helpers import FakeStore

    from app.intent.llm.provider import StaticProvider
    from app.intent.llm.structured_client import StructuredClient

    current_rows = rows or records("retry_pr_evidence.json")
    index = GraphIndex.from_sources(
        repo=REPO,
        normalized_records=current_rows,
        structural_records=structural_records(),
    )
    provider = StaticProvider({"decision_extraction": [envelope(payload)]})
    pipeline = IntentPipeline(
        config=IntentConfig(repo=REPO),
        store=FakeStore(current_rows),
        index=index,
        structured_client=StructuredClient(provider, max_attempts=1),
    )
    return pipeline.run(EvidenceSelector(REPO).select(current_rows))
