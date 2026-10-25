"""Entity extraction contracts.

The tests pin the anti-hallucination boundary: identifiers come from the
catalog, generic words cannot select an entity, and an LLM selection is
rejected unless it names an option that was actually offered.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fixtures.graph_fixtures import (
    CONFIG_MAX_ATTEMPTS_KEY,
    CONFIG_RETRIES_KEY,
    CONFIG_TIMEOUT_KEY,
    DECISION_DEFAULT_KEY,
    REPO,
    SYMBOL_SEND_KEY,
    static_catalog,
)

from app.core.errors import InvalidRecordError
from app.intent.llm.structured_client import StructuredClient
from app.retrieval.config import RetrievalConfig
from app.retrieval.question.entity_catalog import StaticEntityCatalog
from app.retrieval.question.entity_extractor import EntityExtractor
from app.retrieval.question.models import (
    EntityType,
    ExtractionMethod,
    QuestionType,
    TimeConstraintKind,
)


def config(**overrides: Any) -> RetrievalConfig:
    base = RetrievalConfig(
        repo=REPO,
        enable_llm_entity_extraction=False,
    )
    return base.model_copy(update=overrides)


class SelectingProvider:
    """Select the first offered option whose line contains ``needle``."""

    def __init__(self, needle: str) -> None:
        self.needle = needle
        self.calls = 0
        self.prompts: list[str] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        del system, schema, schema_name
        self.calls += 1
        self.prompts.append(user)
        option_id = None
        for line in user.splitlines():
            if line.startswith("O") and self.needle in line:
                option_id = line.split(" | ", 1)[0]
                break
        payload = {
            "entities": (
                [{"option_id": option_id, "text": "client retry behavior"}]
                if option_id
                else []
            ),
            "no_match_reason": None if option_id else "no relevant option",
        }
        return json.dumps(payload)


class InvalidOptionProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        del system, user, schema, schema_name
        self.calls += 1
        return json.dumps(
            {
                "entities": [{"option_id": "O999", "text": "invented"}],
                "no_match_reason": None,
            }
        )


class NormalizedProvider:
    """Legacy response shape; still only accepted if the entity was offered."""

    def __init__(self, normalized: str) -> None:
        self.normalized = normalized
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        del system, user, schema, schema_name
        self.calls += 1
        return json.dumps(
            {
                "entities": [
                    {
                        "type": "symbol",
                        "text": "client retry behavior",
                        "normalized": self.normalized,
                    }
                ],
                "time_constraint": None,
                "requires_supersession_check": False,
            }
        )


class NeverCalledProvider:
    def complete(self, **_: Any) -> str:
        raise AssertionError("LLM must not be called")


def extractor(
    *,
    enable_llm: bool = False,
    client: Any | None = None,
    catalog: StaticEntityCatalog | None = None,
    **config_overrides: Any,
) -> EntityExtractor:
    structured = (
        StructuredClient(client, max_attempts=1) if client is not None else None
    )
    return EntityExtractor(
        catalog or static_catalog(),
        config(
            enable_llm_entity_extraction=enable_llm,
            **config_overrides,
        ),
        structured_client=structured,
    )


def keys(understanding: Any) -> set[str]:
    return {entity.graph_key for entity in understanding.entities}


def test_demo_question_is_deterministic_and_graph_backed() -> None:
    understanding = extractor().extract(
        "Why are retries disabled by default in httpx?"
    )
    assert understanding.question_type is QuestionType.CONFIG_WHY
    assert understanding.extraction_method is ExtractionMethod.DETERMINISTIC
    assert CONFIG_RETRIES_KEY in keys(understanding)
    assert understanding.requires_supersession_check
    assert all(entity.normalized for entity in understanding.entities)


def test_retry_limit_matches_max_attempts_without_an_llm() -> None:
    understanding = extractor().extract("Why is the retry limit three?")
    assert CONFIG_MAX_ATTEMPTS_KEY in keys(understanding)
    assert understanding.extraction_method is ExtractionMethod.DETERMINISTIC


def test_exact_symbol_match_bypasses_the_llm() -> None:
    understanding = extractor(
        enable_llm=True,
        client=NeverCalledProvider(),
    ).extract("Why does Client.send not retry requests?")
    assert SYMBOL_SEND_KEY in keys(understanding)
    assert understanding.question_type is QuestionType.CODE_WHY
    assert understanding.extraction_method is ExtractionMethod.DETERMINISTIC


def test_llm_may_select_a_catalog_option_for_a_paraphrase() -> None:
    provider = SelectingProvider("Client.send")
    understanding = extractor(
        enable_llm=True,
        client=provider,
    ).extract("Why does the client retry behavior matter?")
    assert provider.calls == 1
    assert SYMBOL_SEND_KEY in keys(understanding)
    assert understanding.extraction_method is ExtractionMethod.LLM
    assert "Client.send" in provider.prompts[0]


def test_llm_option_ids_are_validated_against_the_shortlist() -> None:
    provider = InvalidOptionProvider()
    understanding = extractor(
        enable_llm=True,
        client=provider,
    ).extract("Why does the client retry behavior matter?")
    assert provider.calls == 1
    assert "llm_unknown_option:O999" in understanding.warnings
    assert understanding.extraction_method is ExtractionMethod.DETERMINISTIC_FALLBACK
    assert understanding.entities == []


def test_llm_normalized_identifier_must_match_the_shortlist() -> None:
    provider = NormalizedProvider("Client.send")
    understanding = extractor(
        enable_llm=True,
        client=provider,
    ).extract("Why does the client retry behavior matter?")
    assert provider.calls == 1
    assert SYMBOL_SEND_KEY in keys(understanding)
    assert understanding.extraction_method is ExtractionMethod.LLM


def test_llm_cannot_introduce_an_unlisted_normalized_identifier() -> None:
    provider = NormalizedProvider("Evil.send")
    understanding = extractor(
        enable_llm=True,
        client=provider,
    ).extract("Why does the client retry behavior matter?")
    assert provider.calls == 1
    assert "llm_unknown_option:Evil.send" in understanding.warnings
    assert understanding.entities == []


def test_missing_llm_degrades_with_a_warning() -> None:
    understanding = extractor(enable_llm=True).extract(
        "Why does the client retry behavior matter?"
    )
    assert "llm_unavailable" in understanding.warnings
    assert understanding.extraction_method is ExtractionMethod.DETERMINISTIC_FALLBACK


def test_a_generic_word_alone_cannot_select_a_symbol() -> None:
    understanding = extractor().extract(
        "Why does the client behave this way?"
    )
    assert SYMBOL_SEND_KEY not in keys(understanding)


def test_a_single_decision_token_is_not_enough() -> None:
    understanding = extractor().extract("Why is retry important?")
    assert all(
        entity.type is not EntityType.DECISION
        for entity in understanding.entities
    )


def test_decision_claim_match_is_accepted_with_two_distinct_terms() -> None:
    understanding = extractor().extract(
        "Why was the retry default changed?"
    )
    assert DECISION_DEFAULT_KEY in keys(understanding)
    assert understanding.question_type is QuestionType.DECISION_HISTORY


def test_time_constraint_is_explicit_and_stable() -> None:
    understanding = extractor().extract(
        "Why were retries limited before 2024?"
    )
    assert understanding.time_constraint is not None
    assert understanding.time_constraint.kind is TimeConstraintKind.BEFORE
    assert understanding.time_constraint.start is None
    assert understanding.time_constraint.end == "2023-12-31"


def test_empty_catalog_is_reported_not_guessed() -> None:
    understanding = extractor(
        catalog=StaticEntityCatalog(REPO, [])
    ).extract("Why are retries disabled by default?")
    assert understanding.entities == []
    assert "empty_catalog" in understanding.warnings


def test_oversized_question_is_refused() -> None:
    with pytest.raises(InvalidRecordError):
        extractor(max_question_chars=20).extract(
            "Why are retries disabled by default in httpx?"
        )


def test_repeated_runs_are_identical() -> None:
    extractor_instance = extractor()
    first = extractor_instance.extract(
        "Why are retries disabled by default in httpx?"
    )
    second = extractor_instance.extract(
        "Why are retries disabled by default in httpx?"
    )
    assert first == second


def test_timeouts_question_resolves_the_timeout_key() -> None:
    understanding = extractor().extract(
        "Why does the client use separate connect, read, write, and pool timeouts?"
    )
    assert CONFIG_TIMEOUT_KEY in keys(understanding)
    assert understanding.question_type is QuestionType.CONFIG_WHY
