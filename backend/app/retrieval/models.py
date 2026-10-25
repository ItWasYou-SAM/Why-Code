"""Transport-neutral models for the retrieval and answer pipeline.

Nothing in this module talks to Neo4j or an LLM. The pipeline passes these
records between stages, which is what makes the demo answer reproducible:
every stage has a typed input and a typed output.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class QuestionType(StrEnum):
    CONFIG_WHY = "CONFIG_WHY"
    CODE_WHY = "CODE_WHY"
    DECISION_HISTORY = "DECISION_HISTORY"
    OWNERSHIP = "OWNERSHIP"
    SUPERSESSION = "SUPERSESSION"
    GENERAL_CODE_QUESTION = "GENERAL_CODE_QUESTION"


class EntityType(StrEnum):
    CONFIG_KEY = "config_key"
    SYMBOL = "symbol"
    FILE = "file"
    DECISION = "decision"
    PERSON = "person"
    CLAIM = "claim"


class RetrievalSource(StrEnum):
    EXACT = "exact"
    FULLTEXT = "fulltext"
    VECTOR = "vector"
    GRAPH = "graph"


class DecisionState(StrEnum):
    ACTIVE = "active"
    CONTESTED = "contested"
    SUPERSEDED = "superseded"
    REVERSED = "reversed"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


class AnswerStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONTESTED = "contested"


class AnswerConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class EvidenceStrength(StrEnum):
    """Categorical evidence signal for the UI.

    Deliberately not a similarity score: judges see *why* the evidence is
    strong (independent kinds, corroboration), never a cosine number.
    """

    STRONG = "strong"
    MODERATE = "moderate"
    THIN = "thin"
    NONE = "none"


class RefusalMode(StrEnum):
    NO_ENTITY = "NO_ENTITY"
    NO_DECISION = "NO_DECISION"
    CONTESTED = "CONTESTED"
    SUPERSEDED_ONLY = "SUPERSEDED_ONLY"


class MatchSource(StrEnum):
    """How an entity was selected. Only ``EXACT`` is fully trusted."""

    EXACT = "exact"
    LEXICAL = "lexical"
    LLM = "llm"


class ExtractionMethod(StrEnum):
    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"
    LLM = "llm"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"


class TimeConstraintKind(StrEnum):
    BEFORE = "before"
    AFTER = "after"
    DURING = "during"
    BETWEEN = "between"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeConstraint(_StrictModel):
    """An explicit, parseable date range; never a relative phrase."""

    kind: TimeConstraintKind
    start: str | None = None
    end: str | None = None
    raw: str = Field(min_length=1)


class Classification(_StrictModel):
    question_type: QuestionType
    scores: dict[str, float] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)
    requires_supersession_check: bool


class ExtractedEntity(_StrictModel):
    type: EntityType
    text: str = Field(min_length=1)
    normalized: str = Field(min_length=1)
    graph_key: str | None = None
    match: MatchSource | None = None
    score: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("text", "normalized")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = " ".join(value.split()).strip()
        if not value:
            raise ValueError("entity fields must not be blank")
        return value


class QuestionUnderstanding(_StrictModel):
    question: str = Field(min_length=1)
    question_type: QuestionType
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=12)
    time_constraint: TimeConstraint | str | None = None
    requires_supersession_check: bool = True
    repo: str | None = None
    classification: Classification | None = None
    extraction_method: ExtractionMethod | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("question")
    @classmethod
    def _question_clean(cls, value: str) -> str:
        value = " ".join(value.split()).strip()
        if not value:
            raise ValueError("question must not be blank")
        return value

    def as_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["question_type"] = self.question_type.value
        return payload


class RetrievalQuery(_StrictModel):
    exact_identifiers: list[str] = Field(default_factory=list)
    fulltext_query: str = ""
    vector_text: str = ""


class AskParameters(_StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    repo: str | None = None
    max_evidence_items: int = Field(default=8, ge=1, le=20)
    max_answer_tokens: int = Field(default=700, ge=100, le=4000)
    include_graph_path: bool = True
    include_evidence: bool = True

    @field_validator("question")
    @classmethod
    def _question_clean(cls, value: str) -> str:
        value = " ".join(value.split()).strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class DecisionCandidate(_StrictModel):
    """A decision node as seen by one retrieval source.

    ``sources`` records the *rank* the candidate received from each source.
    Raw similarity scores never travel further than the source adapter,
    because WRRF only needs ranks.
    """

    decision_id: str = Field(min_length=1)
    claim: str = ""
    rationale: str = ""
    status: DecisionState = DecisionState.ACTIVE
    confidence: AnswerConfidence = AnswerConfidence.MEDIUM
    target_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    created_at: str | None = None
    sources: dict[RetrievalSource, int] = Field(default_factory=dict)


class EvidenceItem(_StrictModel):
    evidence_id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    excerpt: str = ""
    url: str | None = None
    decision_id: str = ""
    pr_number: int | None = Field(default=None, ge=0)
    issue_number: int | None = Field(default=None, ge=0)
    commit_sha: str | None = None
    author: str | None = None
    created_at: str | None = None
    path: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class GraphContext(_StrictModel):
    decision: DecisionCandidate
    evidence: list[EvidenceItem] = Field(default_factory=list)
    targets: list[str] = Field(default_factory=list)
    pull_requests: list[str] = Field(default_factory=list)
    incidents: list[str] = Field(default_factory=list)
    later_decisions: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)

    def path(self) -> list[str]:
        return build_graph_path(self)


class FusedCandidate(_StrictModel):
    candidate: DecisionCandidate
    score: float = Field(ge=0.0)
    contributions: dict[RetrievalSource, float] = Field(default_factory=dict)


class RerankSignals(_StrictModel):
    graph_path_to_exact_entity: bool = False
    independent_evidence_sources: int = 0
    superseded: bool = False
    review_comment_on_region: bool = False
    target_matches_entity: bool = False
    target_related: bool = True
    stale_without_supersession_check: bool = False
    single_weak_evidence: bool = False


class RankedCandidate(_StrictModel):
    candidate: DecisionCandidate
    fused_score: float = Field(ge=0.0)
    final_score: float
    signals: RerankSignals = Field(default_factory=RerankSignals)
    reasons: list[str] = Field(default_factory=list)


class SupersessionRecord(_StrictModel):
    decision_id: str
    superseded_by: str
    relation: str = "SUPERSEDED_BY"


class EvidenceView(_StrictModel):
    evidence_id: str
    kind: str
    url: str | None = None
    excerpt: str = ""
    alias: str | None = None
    author: str | None = None
    created_at: str | None = None
    pr_number: int | None = None
    issue_number: int | None = None
    commit_sha: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class DecisionView(_StrictModel):
    decision_id: str
    claim: str = ""
    rationale: str = ""
    status: DecisionState = DecisionState.ACTIVE
    confidence: AnswerConfidence = AnswerConfidence.MEDIUM
    target_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    superseded_by: str | None = None
    created_at: str | None = None
    is_historical: bool = False


class PersonView(_StrictModel):
    login: str
    name: str | None = None
    roles: list[str] = Field(default_factory=list)
    evidence_count: int = 0
    first_seen: str | None = None
    last_seen: str | None = None


class NearestCandidate(_StrictModel):
    decision_id: str
    claim: str = ""
    status: DecisionState = DecisionState.UNCERTAIN


class RetrievalTrace(_StrictModel):
    exact_matches: int = 0
    fulltext_candidates: int = 0
    vector_candidates: int = 0
    graph_candidates: int = 0
    fused_candidates: int = 0
    selected_decisions: int = 0


class AnswerDraft(_StrictModel):
    answer_status: AnswerStatus
    answer: str = Field(min_length=1)
    citations: list[str]
    caveats: list[str]
    confidence: AnswerConfidence

    @field_validator("answer")
    @classmethod
    def _answer_clean(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("answer must not be blank")
        return value


class ValidationResult(_StrictModel):
    answer_status: AnswerStatus
    citation_valid: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)
    quote_mismatches: list[str] = Field(default_factory=list)
    final_confidence: AnswerConfidence
    fabricated_details: list[str] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


class RefusalResult(_StrictModel):
    mode: RefusalMode
    answer_status: AnswerStatus
    answer: str
    confidence: AnswerConfidence = AnswerConfidence.NONE
    decision_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    nearest_candidates: list[NearestCandidate] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


class AskResponse(_StrictModel):
    answer_status: AnswerStatus
    answer: str
    confidence: AnswerConfidence
    decision_ids: list[str] = Field(default_factory=list)
    evidence: list[EvidenceView] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)
    graph_path: list[str] = Field(default_factory=list)
    supersession: list[SupersessionRecord] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    nearest_candidates: list[NearestCandidate] = Field(default_factory=list)
    retrieval_trace: RetrievalTrace = Field(default_factory=RetrievalTrace)
    # Presentation signals. They are deliberately categorical: judges see
    # evidence strength and whether the citation gate passed, never a cosine
    # or BM25 number.
    citation_valid: bool = True
    evidence_strength: EvidenceStrength = EvidenceStrength.NONE
    refusal_mode: RefusalMode | None = None
    decisions: list[DecisionView] = Field(default_factory=list)
    people: list[PersonView] = Field(default_factory=list)


_TARGET_ORDER = {
    "ConfigKey": 0,
    "Symbol": 1,
    "CodeRegion": 2,
    "File": 3,
    "Commit": 4,
    "PullRequest": 5,
    "Issue": 6,
    "Decision": 7,
    "Evidence": 8,
}


def build_graph_path(context: GraphContext) -> list[str]:
    """Build the demo path: config/symbol/region → PR → decision → evidence.

    Targets are ordered by the label order a judge expects, not by retrieval
    order, so the same graph always renders the same path.
    """
    targets = sorted(
        dict.fromkeys(context.targets),
        key=lambda item: (_TARGET_ORDER.get(item.split(":", 1)[0], 50), item),
    )
    path = list(targets)
    path.extend(dict.fromkeys(context.pull_requests))
    path.append(f"Decision:{context.decision.decision_id}")
    for item in context.evidence:
        path.append(f"Evidence:{item.evidence_id}")
    return list(dict.fromkeys(path))


__all__ = [
    "AnswerConfidence",
    "AnswerDraft",
    "AnswerStatus",
    "AskResponse",
    "Classification",
    "DecisionCandidate",
    "DecisionState",
    "DecisionView",
    "EntityType",
    "EvidenceItem",
    "EvidenceStrength",
    "EvidenceView",
    "ExtractedEntity",
    "ExtractionMethod",
    "FusedCandidate",
    "GraphContext",
    "MatchSource",
    "NearestCandidate",
    "PersonView",
    "QuestionType",
    "QuestionUnderstanding",
    "RankedCandidate",
    "RefusalMode",
    "RefusalResult",
    "RerankSignals",
    "RetrievalQuery",
    "RetrievalSource",
    "RetrievalTrace",
    "SupersessionRecord",
    "TimeConstraint",
    "TimeConstraintKind",
    "ValidationResult",
    "build_graph_path",
]
