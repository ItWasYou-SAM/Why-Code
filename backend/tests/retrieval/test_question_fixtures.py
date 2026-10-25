"""The curated question fixtures are executable, not documentation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.retrieval.models import EntityType, QuestionType
from app.retrieval.question.entity_extractor import (
    EntityExtractor,
    EntityIndex,
    KnownEntity,
)
from app.retrieval.question.question_classifier import QuestionClassifier

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "question_fixtures.json"
INDEX = EntityIndex(
    entities=[
        KnownEntity(
            type=EntityType.CONFIG_KEY,
            identifier="retry.max_attempts",
            aliases=("retry limit", "retries", "retry"),
        ),
        KnownEntity(
            type=EntityType.SYMBOL,
            identifier="Client.send",
            aliases=("client send",),
        ),
        KnownEntity(
            type=EntityType.CONFIG_KEY,
            identifier="timeout",
            aliases=("timeouts", "pool timeout"),
        ),
    ]
)


def _cases() -> list[dict[str, object]]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["question"]))
def test_fixture_classification(case: dict[str, object]) -> None:
    expected = QuestionType(str(case["question_type"]))
    assert QuestionClassifier().classify(str(case["question"])) is expected


@pytest.mark.parametrize("case", _cases(), ids=lambda case: str(case["question"]))
def test_fixture_expected_entities_are_extracted(case: dict[str, object]) -> None:
    result = EntityExtractor(index=INDEX).extract(
        str(case["question"]), QuestionType(str(case["question_type"]))
    )
    extracted = {item.normalized for item in result.entities}
    for raw in case["entities"]:  # type: ignore[union-attr]
        assert raw["normalized"] in extracted
