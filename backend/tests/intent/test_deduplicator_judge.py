"""Phase 7 duplicate adjudication contracts."""

from __future__ import annotations

import json

from app.intent.llm.provider import StaticProvider
from app.intent.llm.schemas import DuplicateVerdict
from app.intent.llm.structured_client import StructuredClient
from app.intent.models import Confidence, DecisionIntent, ResolvedTarget, TargetType
from app.intent.validation.deduplicator import DuplicateCandidate
from app.intent.validation.deduplicator_judge import (
    DuplicateJudge,
    render_duplicate_prompt,
)

REPO = "encode/httpx"
CONFIG = "httpx:config:httpx/_config.py:retries"


def candidate() -> DuplicateCandidate:
    earlier = DecisionIntent(
        id="dec_earlier",
        repo=REPO,
        pr_number=100,
        claim="Retries are limited to connection errors.",
        rationale="Retried writes can duplicate server effects.",
        targets=[
            ResolvedTarget(
                type=TargetType.CONFIG_KEY, identifier=CONFIG, graph_key=CONFIG
            )
        ],
        evidence_ids=["httpx:commit:1"],
        confidence=Confidence.MEDIUM,
    )
    later = DecisionIntent(
        id="dec_later",
        repo=REPO,
        pr_number=200,
        claim="Retries are only allowed for connection failures.",
        rationale="Retried writes can duplicate server effects.",
        targets=[
            ResolvedTarget(
                type=TargetType.CONFIG_KEY, identifier=CONFIG, graph_key=CONFIG
            )
        ],
        evidence_ids=["httpx:commit:2"],
        confidence=Confidence.MEDIUM,
    )
    return DuplicateCandidate(
        earlier=earlier,
        later=later,
        similarity=0.91,
        exact=False,
        temporal_compatible=True,
        target_keys=(CONFIG,),
        evidence_lines=(
            ("httpx:commit:1", "Only retry connection errors."),
            ("httpx:commit:2", "Retries are opt-in for connect failures."),
        ),
        context_evidence_ids=("httpx:commit:1", "httpx:commit:2"),
        reason="near-identical claim over the same target(s) in different PRs",
    )


def payload(**overrides: object) -> str:
    data = {
        "earlier_intent_id": "dec_earlier",
        "later_intent_id": "dec_later",
        "relationship": "merge",
        "evidence_ids": ["httpx:commit:2"],
        "explanation": "Both statements describe the same narrow retry policy.",
    }
    data.update(overrides)
    return json.dumps(data)


def judge(reply: str, *, attempts: int = 1) -> DuplicateJudge:
    provider = StaticProvider({"duplicate_verdict": [reply]})
    return DuplicateJudge(StructuredClient(provider, max_attempts=attempts))


class TestVerdictSchema:
    def test_schema_is_strict_and_uses_the_planned_enum(self) -> None:
        schema = DuplicateVerdict.model_json_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "earlier_intent_id",
            "later_intent_id",
            "relationship",
            "evidence_ids",
            "explanation",
        }
        relationships = schema["properties"]["relationship"]["enum"]
        assert set(relationships) == {
            "merge",
            "keep_separate",
            "conflicts",
            "insufficient_evidence",
        }

    def test_evidence_list_is_required_and_nonempty(self) -> None:
        schema = DuplicateVerdict.model_json_schema()
        evidence = schema["properties"]["evidence_ids"]
        assert evidence["minItems"] == 1


class TestPrompt:
    def test_prompt_contains_both_claims_targets_and_evidence(self) -> None:
        system, user = render_duplicate_prompt(candidate())
        assert "merge" in system
        assert "Retries are limited to connection errors." in user
        assert "Retries are only allowed for connection failures." in user
        assert CONFIG in user
        assert "[httpx:commit:2]" in user


class TestJudge:
    def test_valid_verdict_is_returned(self) -> None:
        verdict = judge(payload()).judge(candidate())
        assert verdict.relationship == "merge"
        assert verdict.evidence_ids == ["httpx:commit:2"]

    def test_wrong_pair_becomes_insufficient_evidence(self) -> None:
        verdict = judge(
            payload(earlier_intent_id="dec_other")
        ).judge(candidate())
        assert verdict.relationship == "insufficient_evidence"
        assert "does not name" in verdict.explanation

    def test_evidence_outside_the_candidate_becomes_insufficient(self) -> None:
        verdict = judge(
            payload(evidence_ids=["httpx:commit:outside"])
        ).judge(candidate())
        assert verdict.relationship == "insufficient_evidence"
        assert "outside the candidate bundle" in verdict.explanation

    def test_provider_failure_is_insufficient_not_a_merge(self) -> None:
        verdict = judge("not json").judge(candidate())
        assert verdict.relationship == "insufficient_evidence"
        assert verdict.evidence_ids
