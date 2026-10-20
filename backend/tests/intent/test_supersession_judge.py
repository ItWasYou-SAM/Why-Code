"""Phase 6 supersession adjudication contracts."""

from __future__ import annotations

import json

from intent_helpers import REPO

from app.intent.llm.provider import StaticProvider
from app.intent.llm.schemas import SupersessionVerdict
from app.intent.llm.structured_client import StructuredClient
from app.intent.models import Confidence, DecisionIntent, ResolvedTarget, TargetType
from app.intent.validation.supersession import SupersessionCandidate
from app.intent.validation.supersession_judge import (
    SupersessionJudge,
    render_supersession_prompt,
)

CONFIG = "httpx:config:httpx/_config.py:retries"


def candidate() -> SupersessionCandidate:
    earlier = DecisionIntent(
        id="dec_earlier",
        repo=REPO,
        pr_number=100,
        claim="Retries are enabled by default.",
        targets=[
            ResolvedTarget(
                type=TargetType.CONFIG_KEY, identifier=CONFIG, graph_key=CONFIG
            )
        ],
        evidence_ids=["httpx:review_comment:1"],
        confidence=Confidence.MEDIUM,
    )
    later = DecisionIntent(
        id="dec_later",
        repo=REPO,
        pr_number=200,
        claim="Retries are disabled by default.",
        targets=[
            ResolvedTarget(
                type=TargetType.CONFIG_KEY, identifier=CONFIG, graph_key=CONFIG
            )
        ],
        evidence_ids=["httpx:commit:2"],
        confidence=Confidence.MEDIUM,
    )
    return SupersessionCandidate(
        earlier=earlier,
        later=later,
        targets=(CONFIG,),
        signals=("reversal_language",),
        earlier_date="2024-01-01T00:00:00Z",
        later_date="2024-06-01T00:00:00Z",
        evidence_lines=(
            ("httpx:review_comment:1", "Retries are enabled by default."),
            ("httpx:commit:2", "Disable retries by default."),
        ),
    )


def payload(**overrides: object) -> str:
    data = {
        "earlier_decision_id": "dec_earlier",
        "later_decision_id": "dec_later",
        "relationship": "reverses",
        "evidence_ids": ["httpx:commit:2"],
        "explanation": "The later commit disables the default.",
    }
    data.update(overrides)
    return json.dumps(data)


def judge(reply: str, *, attempts: int = 1) -> SupersessionJudge:
    provider = StaticProvider({"supersession_verdict": [reply]})
    return SupersessionJudge(
        StructuredClient(provider, max_attempts=attempts)
    )


class TestVerdictSchema:
    def test_schema_is_strict_and_uses_the_planned_enum(self) -> None:
        schema = SupersessionVerdict.model_json_schema()
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {
            "earlier_decision_id",
            "later_decision_id",
            "relationship",
            "evidence_ids",
            "explanation",
        }
        relationships = schema["properties"]["relationship"]["enum"]
        assert set(relationships) == {
            "reverses",
            "revises",
            "supersedes",
            "unrelated",
            "insufficient_evidence",
        }
        evidence = schema["properties"]["evidence_ids"]
        assert evidence["minItems"] == 1


class TestPrompt:
    def test_prompt_names_the_pair_targets_and_evidence(self) -> None:
        system, user = render_supersession_prompt(candidate())
        assert "reverses" in system
        assert "insufficient_evidence" in system
        assert "Return one of: reverses, revises, supersedes" in user
        assert "dec_earlier" in user
        assert "dec_later" in user
        assert CONFIG in user
        assert "httpx:commit:2" in user
        assert "reversal_language" in user


class TestJudge:
    def test_valid_verdict_is_returned(self) -> None:
        result = judge(payload()).judge(candidate())
        assert result.relationship == "reverses"
        assert result.evidence_ids == ["httpx:commit:2"]

    def test_verdict_for_another_pair_is_insufficient(self) -> None:
        result = judge(payload(earlier_decision_id="dec_other")).judge(
            candidate()
        )
        assert result.relationship == "insufficient_evidence"

    def test_verdict_citing_outside_evidence_is_insufficient(self) -> None:
        result = judge(
            payload(evidence_ids=["httpx:commit:invented"])
        ).judge(candidate())
        assert result.relationship == "insufficient_evidence"

    def test_invalid_schema_relationship_is_insufficient(self) -> None:
        result = judge(payload(relationship="maybe")).judge(candidate())
        assert result.relationship == "insufficient_evidence"
        assert "judge failed" in result.explanation
