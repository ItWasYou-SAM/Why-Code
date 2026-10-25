"""Question class contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.retrieval.question.models import EntityType, QuestionType
from app.retrieval.question.question_classifier import QuestionClassifier

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CASES = json.loads((FIXTURES / "question_fixtures.json").read_text("utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[row["case_id"] for row in CASES])
def test_classifier_matches_fixture(case: dict[str, object]) -> None:
    entity_types = {
        EntityType(value) for value in case["entity_types"]  # type: ignore[union-attr]
    }
    result = QuestionClassifier().classify_details(
        case["question"],  # type: ignore[arg-type]
        entity_types=entity_types,
    )
    assert result.question_type.value == case["question_type"]
    assert (
        result.requires_supersession_check
        is case["requires_supersession_check"]
    )


def test_ownership_outranks_config_vocabulary() -> None:
    result = QuestionClassifier().classify_details(
        "Who should I ask about the retry timeout default?",
        entity_types={EntityType.CONFIG_KEY},
    )
    assert result.question_type is QuestionType.OWNERSHIP
    assert "ownership_phrase" in result.reasons
    assert not result.requires_supersession_check


def test_supersession_outranks_config_vocabulary() -> None:
    result = QuestionClassifier().classify_details(
        "Was the retry default reversed?",
        entity_types={EntityType.CONFIG_KEY},
    )
    assert result.question_type is QuestionType.SUPERSESSION
    assert "supersession_term" in result.reasons


def test_config_outranks_code_when_a_config_key_matched() -> None:
    result = QuestionClassifier().classify_details(
        "Why is the Client.send timeout default five?",
        entity_types={EntityType.SYMBOL, EntityType.CONFIG_KEY},
    )
    assert result.question_type is QuestionType.CONFIG_WHY
    assert "config_key_entity" in result.reasons


def test_unknown_question_defaults_to_general() -> None:
    result = QuestionClassifier().classify_details(
        "Why does httpx use a purple logo?"
    )
    assert result.question_type is QuestionType.GENERAL_CODE_QUESTION
    assert result.reasons == ["default_general_question"]
    assert result.scores[QuestionType.GENERAL_CODE_QUESTION.value] == 0.0


def test_classifier_is_stable_across_runs() -> None:
    classifier = QuestionClassifier()
    first = classifier.classify_details("Why are retries disabled by default?")
    second = classifier.classify_details("Why are retries disabled by default?")
    assert first == second
