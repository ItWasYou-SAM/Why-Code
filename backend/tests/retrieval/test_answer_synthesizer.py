"""Synthesis is constrained: one pack, one schema, one retry, then refusal."""

from __future__ import annotations

from retrieval_fixtures import (
    FakeGenerator,
    answer_json,
    candidate,
    context,
    ranked,
    understanding,
)

from app.retrieval.answer.answer_synthesizer import (
    ANSWER_SCHEMA_NAME,
    AnswerSynthesizer,
)
from app.retrieval.answer.prompt_templates import (
    ANSWER_SCHEMA,
    ANSWER_SYSTEM_PROMPT,
)
from app.retrieval.config import RetrievalConfig
from app.retrieval.context.evidence_packer import EvidencePacker
from app.retrieval.models import AnswerConfidence, AnswerStatus


def _pack(evidence_context=None):
    return EvidencePacker().pack(
        understanding=understanding(),
        ranked=[ranked()],
        contexts={
            "dec_001": evidence_context or context(candidate("dec_001"))
        },
    )


def test_valid_answer_is_returned_and_schema_is_passed() -> None:
    generator = FakeGenerator({ANSWER_SCHEMA_NAME: answer_json()})
    draft = AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.ANSWERED
    assert draft.answer == "HTTPX disables retries by default [E1]."
    assert len(generator.calls) == 1
    call = generator.calls[0]
    assert call.schema_name == ANSWER_SCHEMA_NAME
    assert call.schema["additionalProperties"] is False
    assert call.schema["required"] == [
        "answer_status",
        "answer",
        "citations",
        "caveats",
        "confidence",
    ]
    assert call.system == ANSWER_SYSTEM_PROMPT


def test_provider_schema_is_the_documented_contract() -> None:
    assert ANSWER_SCHEMA == {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "answer_status",
            "answer",
            "citations",
            "caveats",
            "confidence",
        ],
        "properties": {
            "answer_status": {
                "type": "string",
                "enum": ["answered", "insufficient_evidence", "contested"],
            },
            "answer": {"type": "string"},
            "citations": {"type": "array", "items": {"type": "string"}},
            "caveats": {"type": "array", "items": {"type": "string"}},
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low", "none"],
            },
        },
    }


def test_system_prompt_contains_every_rule() -> None:
    for rule in (
        "1. Use only the supplied evidence.",
        "2. Cite every factual statement with evidence IDs.",
        "3. If evidence conflicts, explain the conflict.",
        "4. If a decision was reversed or revised, say so.",
        "5. If evidence is insufficient, return INSUFFICIENT_EVIDENCE.",
        "6. Never invent file names, PR numbers, people, dates, or quotes.",
        "7. Do not use knowledge outside the supplied context.",
        "8. Be concise and technical.",
    ):
        assert rule in ANSWER_SYSTEM_PROMPT


def test_prompt_contains_the_pack_and_the_no_outside_knowledge_rules() -> None:
    generator = FakeGenerator({ANSWER_SCHEMA_NAME: answer_json()})
    AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    user = generator.calls[0].user
    assert "EVIDENCE PACK:" in user
    assert "Why are retries disabled by default in httpx?" in user
    assert "under approximately 700 tokens" in user
    assert "Use only the supplied evidence" in generator.calls[0].system
    assert "Never invent file names" in generator.calls[0].system
    assert "Do not use knowledge outside the supplied context." in generator.calls[0].system


def test_user_prompt_names_every_required_field() -> None:
    generator = FakeGenerator({ANSWER_SCHEMA_NAME: answer_json()})
    AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    user = generator.calls[0].user
    assert "answer_status: answered | insufficient_evidence | contested" in user
    assert "- answer\n" in user
    assert "citations: list of evidence IDs used" in user
    assert "- caveats\n" in user
    assert "confidence: high | medium | low | none" in user


def test_contested_answer_status_is_preserved() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: answer_json(
                status="contested",
                answer="The evidence conflicts [E1].",
                confidence="medium",
            )
        }
    )
    draft = AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.CONTESTED
    assert draft.confidence is AnswerConfidence.MEDIUM


def test_answer_token_budget_is_sent_to_the_model() -> None:
    generator = FakeGenerator({ANSWER_SCHEMA_NAME: answer_json()})
    AnswerSynthesizer(
        generator, RetrievalConfig(max_answer_tokens=250)
    ).synthesize(pack=_pack(), understanding=understanding())
    assert "under approximately 250 tokens" in generator.calls[0].user


def test_answered_without_citations_is_retried_with_strict_prompt() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: [
                answer_json(citations=()),
                answer_json(),
            ]
        }
    )
    draft = AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.ANSWERED
    assert len(generator.calls) == 2
    assert "STRICT CORRECTION" in generator.calls[1].user
    assert "Every sentence" in generator.calls[1].user


def test_malformed_json_is_retried_once_then_refused() -> None:
    generator = FakeGenerator(
        {ANSWER_SCHEMA_NAME: ["not json", "still not json"]}
    )
    draft = AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert draft.confidence is AnswerConfidence.NONE
    assert draft.citations == []
    assert len(generator.calls) == 2
    assert any("validation failed" in caveat for caveat in draft.caveats)


def test_schema_violation_is_treated_as_a_failed_attempt() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: [
                '{"answer_status": "answered"}',
                answer_json(),
            ]
        }
    )
    draft = AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.ANSWERED
    assert len(generator.calls) == 2


def test_unknown_schema_field_fails_the_attempt() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: [
                '{"answer_status": "answered", "answer": "x [E1]", '
                '"citations": ["E1"], "caveats": [], "confidence": "low", '
                '"made_up": true}',
                answer_json(),
            ]
        }
    )
    draft = AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.ANSWERED
    assert len(generator.calls) == 2


def test_provider_error_is_turned_into_a_refusal() -> None:
    class BrokenGenerator:
        def complete(self, **kwargs):
            raise ValueError("provider down")

    draft = AnswerSynthesizer(BrokenGenerator()).synthesize(
        pack=_pack(), understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert draft.confidence is AnswerConfidence.NONE


def test_model_insufficient_response_is_normalized_to_no_confidence() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: answer_json(
                status="insufficient_evidence",
                answer="INSUFFICIENT_EVIDENCE",
                citations=(),
                confidence="medium",
            )
        }
    )
    draft = AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert draft.confidence is AnswerConfidence.NONE


def test_empty_pack_refuses_without_calling_the_model() -> None:
    generator = FakeGenerator({})
    empty_context = context(candidate("dec_001"), evidence_items=[])
    pack = _pack(empty_context)
    draft = AnswerSynthesizer(generator).synthesize(
        pack=pack, understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert generator.calls == []


def test_fenced_json_is_accepted() -> None:
    generator = FakeGenerator(
        {ANSWER_SCHEMA_NAME: f"```json\n{answer_json()}\n```"}
    )
    draft = AnswerSynthesizer(generator).synthesize(
        pack=_pack(), understanding=understanding()
    )
    assert draft.answer_status is AnswerStatus.ANSWERED


def test_attempts_are_configurable_but_capped_at_two() -> None:
    generator = FakeGenerator(
        {ANSWER_SCHEMA_NAME: ["bad", "bad", answer_json()]}
    )
    draft = AnswerSynthesizer(
        generator, RetrievalConfig(answer_attempts=2)
    ).synthesize(pack=_pack(), understanding=understanding())
    assert draft.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert len(generator.calls) == 2
