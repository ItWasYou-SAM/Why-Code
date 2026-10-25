"""HTTP request/response schemas for the answer service."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.retrieval.models import (
    AskParameters,
    AskResponse,
    EvidenceView,
    GraphContext,
    NearestCandidate,
    SupersessionRecord,
)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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

    def parameters(self) -> AskParameters:
        return AskParameters(
            question=self.question,
            repo=self.repo,
            max_evidence_items=self.max_evidence_items,
            max_answer_tokens=self.max_answer_tokens,
            include_graph_path=self.include_graph_path,
            include_evidence=self.include_evidence,
        )


class DecisionDetail(BaseModel):
    decision_id: str
    claim: str
    rationale: str
    status: str
    confidence: str
    targets: list[str] = Field(default_factory=list)
    evidence: list[EvidenceView] = Field(default_factory=list)
    graph_path: list[str] = Field(default_factory=list)
    supersession: list[SupersessionRecord] = Field(default_factory=list)

    @classmethod
    def from_context(cls, context: GraphContext) -> DecisionDetail:
        return cls(
            decision_id=context.decision.decision_id,
            claim=context.decision.claim,
            rationale=context.decision.rationale,
            status=context.decision.status.value,
            confidence=context.decision.confidence.value,
            targets=context.targets or context.decision.target_ids,
            evidence=[
                EvidenceView(
                    evidence_id=item.evidence_id,
                    kind=item.kind,
                    url=item.url,
                    excerpt=" ".join(item.excerpt.split()),
                )
                for item in context.evidence
            ],
            graph_path=context.path(),
            supersession=[
                SupersessionRecord(
                    decision_id=context.decision.decision_id,
                    superseded_by=later,
                )
                for later in context.later_decisions
            ],
        )


class EvidenceDetail(BaseModel):
    evidence_id: str
    kind: str
    url: str | None = None
    excerpt: str = ""
    author: str | None = None
    created_at: str | None = None
    pr_number: int | None = None
    issue_number: int | None = None
    commit_sha: str | None = None
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None


class EntityHistoryResponse(BaseModel):
    entity_id: str
    decisions: list[NearestCandidate] = Field(default_factory=list)


class FeedbackVerdict(StrEnum):
    HELPFUL = "helpful"
    WRONG = "wrong"
    MISSING_EVIDENCE = "missing_evidence"


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    verdict: FeedbackVerdict
    comment: str = Field(default="", max_length=4000)
    decision_ids: list[str] = Field(default_factory=list)
    answer_id: str | None = None


class FeedbackResponse(BaseModel):
    feedback_id: str
    status: str = "recorded"


__all__ = [
    "AskRequest",
    "AskResponse",
    "DecisionDetail",
    "EntityHistoryResponse",
    "EvidenceDetail",
    "FeedbackRequest",
    "FeedbackResponse",
    "FeedbackVerdict",
]
