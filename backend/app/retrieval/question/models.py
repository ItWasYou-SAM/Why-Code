"""Question-understanding contracts.

The pipeline-wide records live in :mod:`app.retrieval.models`; this module
re-exports them so question code and the retrieval service cannot drift into
two different entity or question enums. Only the catalog option and the
LLM-selection schema are specific to Phase 1.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.models import (
    Classification,
    EntityType,
    ExtractedEntity,
    ExtractionMethod,
    MatchSource,
    QuestionType,
    QuestionUnderstanding,
    TimeConstraint,
    TimeConstraintKind,
)

ENTITY_TYPE_ORDER = {
    EntityType.CONFIG_KEY: 0,
    EntityType.SYMBOL: 1,
    EntityType.FILE: 2,
    EntityType.DECISION: 3,
    EntityType.PERSON: 4,
    EntityType.CLAIM: 5,
}

QUESTION_TYPE_ORDER = {
    QuestionType.OWNERSHIP: 0,
    QuestionType.SUPERSESSION: 1,
    QuestionType.DECISION_HISTORY: 2,
    QuestionType.CONFIG_WHY: 3,
    QuestionType.CODE_WHY: 4,
    QuestionType.GENERAL_CODE_QUESTION: 5,
}


class CatalogEntity(BaseModel):
    """One entity the extractor is allowed to select.

    ``graph_key`` is the node identity used by Neo4j. The extractor never
    invents one. ``claim`` is only populated for decision-shaped nodes and is
    matched conservatively, separately from the identifier.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    graph_key: str = Field(min_length=1)
    type: EntityType
    identifier: str = Field(min_length=1)
    display_name: str | None = None
    path: str | None = None
    claim: str | None = None
    status: str | None = None
    pr_number: int | None = None
    source_label: str | None = None
    aliases: tuple[str, ...] = ()


class LLMEntityChoice(BaseModel):
    """One option selected from the shortlist sent to the model.

    The option-id form is the preferred protocol. The normalized-identifier
    form is accepted for compatibility, but it is resolved against the same
    shortlist; a model cannot introduce an entity that was not offered.
    """

    model_config = ConfigDict(extra="forbid")

    option_id: str | None = Field(default=None, max_length=32)
    type: EntityType | None = None
    text: str = Field(min_length=1, max_length=200)
    normalized: str | None = Field(default=None, max_length=500)


class LLMEntitySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entities: list[LLMEntityChoice] = Field(default_factory=list, max_length=8)
    no_match_reason: str | None = Field(default=None, max_length=300)
    # Legacy response fields are accepted but deliberately ignored: time and
    # supersession flags are computed deterministically.
    time_constraint: str | None = None
    requires_supersession_check: bool | None = None


__all__ = [
    "ENTITY_TYPE_ORDER",
    "QUESTION_TYPE_ORDER",
    "CatalogEntity",
    "Classification",
    "EntityType",
    "ExtractedEntity",
    "ExtractionMethod",
    "LLMEntityChoice",
    "LLMEntitySelection",
    "MatchSource",
    "QuestionType",
    "QuestionUnderstanding",
    "TimeConstraint",
    "TimeConstraintKind",
]
