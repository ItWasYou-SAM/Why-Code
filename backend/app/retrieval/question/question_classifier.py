"""Deterministic question classifier.

The classifier is a pure function of the question text and the entity types
the deterministic matcher accepted. It never calls a model. Every weight and
phrase is visible so a misrouted demo question can be explained rather than
hand-waved.
"""

from __future__ import annotations

import re
from collections.abc import Collection

from app.retrieval.question.models import (
    QUESTION_TYPE_ORDER,
    Classification,
    EntityType,
    QuestionType,
)
from app.retrieval.question.normalization import content_tokens

_OWNERSHIP_PHRASES = (
    ("who should i ask", 14.0),
    ("who should we ask", 14.0),
    ("who do i ask", 13.0),
    ("who can i ask", 13.0),
    ("who is responsible", 12.0),
    ("who owns", 12.0),
    ("who maintains", 12.0),
    ("point of contact", 12.0),
    ("who knows", 10.0),
    ("who reviewed", 9.0),
    ("who worked on", 8.0),
)

_OWNERSHIP_TERMS = (
    ("maintainer", 3.0),
    ("maintainers", 3.0),
    ("owner", 2.0),
    ("owners", 2.0),
    ("expert", 2.0),
    ("experts", 2.0),
)

_SUPERSESSION_PHRASES = (
    ("no longer", 9.0),
    ("used to", 8.0),
    ("changed back", 12.0),
    ("replaced by", 10.0),
    ("is it still", 9.0),
    ("still current", 9.0),
)

_SUPERSESSION_TERMS = (
    ("reversed", 12.0),
    ("revert", 12.0),
    ("reverted", 12.0),
    ("reversal", 12.0),
    ("superseded", 12.0),
    ("supersession", 10.0),
    ("overturned", 12.0),
    ("revised", 7.0),
    ("revision", 6.0),
    ("deprecated", 7.0),
    ("originally", 4.0),
    ("still", 2.0),
    ("current", 2.0),
)

_DECISION_HISTORY_PHRASES = (
    ("why was this changed", 8.0),
    ("why was it changed", 8.0),
    ("why was this introduced", 7.0),
    ("reason for the change", 7.0),
    ("reason for this change", 7.0),
    ("what changed", 7.0),
    ("decision history", 7.0),
    ("who changed", 7.0),
    ("who added", 6.0),
    ("who removed", 6.0),
    ("which pr", 6.0),
    ("what pr", 6.0),
    ("pr #", 4.0),
    ("introduced in", 4.0),
    ("history of", 4.0),
    ("pull request", 2.0),
)

_DECISION_HISTORY_TERMS = (
    ("history", 5.0),
    ("rationale", 5.0),
    ("decision", 3.0),
    ("decisions", 3.0),
    ("changed", 5.0),
    ("change", 4.0),
    ("modified", 4.0),
)

_CONFIG_PHRASES = (
    ("environment variable", 4.0),
    ("env var", 4.0),
    ("default value", 3.0),
    ("by default", 2.0),
)

_CONFIG_TERMS = (
    ("config", 3.0),
    ("configuration", 3.0),
    ("setting", 3.0),
    ("settings", 3.0),
    ("option", 2.0),
    ("options", 2.0),
    ("default", 2.0),
    ("defaults", 2.0),
    ("enabled", 2.0),
    ("disabled", 2.0),
    ("limit", 2.0),
    ("limits", 2.0),
    ("retry", 2.0),
    ("retries", 2.0),
    ("attempt", 1.0),
    ("attempts", 1.0),
    ("threshold", 2.0),
    ("maximum", 2.0),
    ("minimum", 2.0),
    ("timeout", 3.0),
    ("timeouts", 3.0),
    ("flag", 2.0),
    ("flags", 2.0),
    ("environment", 3.0),
    ("env", 2.0),
    ("variable", 2.0),
    ("value", 1.0),
)

_CODE_PHRASES = (
    ("how does it work", 3.0),
    ("under the hood", 3.0),
)

_CODE_TERMS = (
    ("function", 3.0),
    ("functions", 3.0),
    ("method", 3.0),
    ("methods", 3.0),
    ("class", 2.0),
    ("classes", 2.0),
    ("implementation", 3.0),
    ("implemented", 3.0),
    ("code", 2.0),
    ("algorithm", 2.0),
    ("behavior", 2.0),
    ("behaviour", 2.0),
    ("calculate", 2.0),
    ("parse", 2.0),
    ("parser", 2.0),
    ("work", 1.0),
    ("works", 1.0),
)

_GENERAL_PHRASES = (
    ("how does", 4.0),
    ("what does", 3.0),
    ("where is", 4.0),
    ("where are", 4.0),
    ("walk me through", 3.0),
    ("tell me about", 2.0),
    ("what is", 2.0),
    ("what are", 2.0),
)

_GENERAL_TERMS = (("explain", 2.0),)

# Dotted or path-like identifier syntax is a reliable code-question signal
# even before the catalog has supplied an entity type.
_IDENTIFIER_SYNTAX = re.compile(
    r"\b[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+\b"
)
_PATH_SYNTAX = re.compile(r"\b[\w.-]+/[\w./-]+\b")

_SUPERSESSION_CLASSES = frozenset(
    {
        QuestionType.CONFIG_WHY,
        QuestionType.CODE_WHY,
        QuestionType.DECISION_HISTORY,
        QuestionType.SUPERSESSION,
    }
)


class QuestionClassifier:
    def classify(
        self,
        question: str,
        *,
        entity_types: Collection[EntityType] = (),
    ) -> QuestionType:
        """Return just the route, which is the stable pipeline contract."""
        return self.classify_details(
            question, entity_types=entity_types
        ).question_type

    def classify_details(
        self,
        question: str,
        *,
        entity_types: Collection[EntityType] = (),
    ) -> Classification:
        """Return the route plus the deterministic evidence for it."""
        text = " ".join(question.lower().split())
        tokens = set(content_tokens(question))
        scores = {question_type: 0.0 for question_type in QuestionType}
        reasons: dict[QuestionType, list[str]] = {
            question_type: [] for question_type in QuestionType
        }

        def add(
            question_type: QuestionType,
            weight: float,
            reason: str,
        ) -> None:
            scores[question_type] += weight
            if reason not in reasons[question_type]:
                reasons[question_type].append(reason)

        def phrases(
            question_type: QuestionType,
            values: tuple[tuple[str, float], ...],
            reason: str,
        ) -> None:
            for phrase, weight in values:
                if phrase in text:
                    add(question_type, weight, reason)

        def terms(
            question_type: QuestionType,
            values: tuple[tuple[str, float], ...],
            reason: str,
        ) -> None:
            for term, weight in values:
                if term in tokens:
                    add(question_type, weight, reason)

        phrases(QuestionType.OWNERSHIP, _OWNERSHIP_PHRASES, "ownership_phrase")
        terms(QuestionType.OWNERSHIP, _OWNERSHIP_TERMS, "ownership_term")
        phrases(
            QuestionType.SUPERSESSION,
            _SUPERSESSION_PHRASES,
            "supersession_phrase",
        )
        terms(
            QuestionType.SUPERSESSION,
            _SUPERSESSION_TERMS,
            "supersession_term",
        )
        phrases(
            QuestionType.DECISION_HISTORY,
            _DECISION_HISTORY_PHRASES,
            "decision_history_phrase",
        )
        terms(
            QuestionType.DECISION_HISTORY,
            _DECISION_HISTORY_TERMS,
            "decision_history_term",
        )
        phrases(QuestionType.CONFIG_WHY, _CONFIG_PHRASES, "config_phrase")
        terms(QuestionType.CONFIG_WHY, _CONFIG_TERMS, "config_vocabulary")
        phrases(QuestionType.CODE_WHY, _CODE_PHRASES, "code_phrase")
        terms(QuestionType.CODE_WHY, _CODE_TERMS, "code_vocabulary")
        phrases(
            QuestionType.GENERAL_CODE_QUESTION,
            _GENERAL_PHRASES,
            "general_phrase",
        )
        terms(
            QuestionType.GENERAL_CODE_QUESTION,
            _GENERAL_TERMS,
            "general_term",
        )

        if EntityType.CONFIG_KEY in entity_types:
            add(QuestionType.CONFIG_WHY, 5.0, "config_key_entity")
        if EntityType.SYMBOL in entity_types:
            add(QuestionType.CODE_WHY, 4.0, "symbol_entity")
        if EntityType.FILE in entity_types:
            add(QuestionType.CODE_WHY, 4.0, "file_entity")
        if EntityType.DECISION in entity_types:
            add(
                QuestionType.DECISION_HISTORY,
                4.0,
                "decision_entity",
            )
        if EntityType.PERSON in entity_types:
            add(QuestionType.OWNERSHIP, 5.0, "person_entity")
        if _IDENTIFIER_SYNTAX.search(question) or _PATH_SYNTAX.search(question):
            add(QuestionType.CODE_WHY, 4.0, "identifier_syntax")

        rounded = {
            question_type.value: round(score, 3)
            for question_type, score in scores.items()
        }
        best_score = max(scores.values())
        if best_score <= 0.0:
            chosen = QuestionType.GENERAL_CODE_QUESTION
            chosen_reasons = ["default_general_question"]
        else:
            tied = [
                question_type
                for question_type, score in scores.items()
                if score == best_score
            ]
            chosen = min(tied, key=lambda item: QUESTION_TYPE_ORDER[item])
            chosen_reasons = reasons[chosen] or ["deterministic_signal"]

        return Classification(
            question_type=chosen,
            scores=rounded,
            reasons=chosen_reasons,
            requires_supersession_check=chosen in _SUPERSESSION_CLASSES,
        )


__all__ = ["QuestionClassifier"]
