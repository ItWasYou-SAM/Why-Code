"""Schemas for the judge-facing demo flow."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.retrieval.models import AskResponse


class DemoQuestion(BaseModel):
    question_id: str
    question: str
    focus: str
    expectation: str
    expected_status: str
    expected_decision_id: str | None = None
    expected_refusal_mode: str | None = None


class DemoScriptResponse(BaseModel):
    repo: str
    mode: str = "deterministic-fixture"
    latency_budget_ms: int
    target_latency_ms: int
    questions: list[DemoQuestion]


class DemoAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    repo: str | None = None

    @field_validator("question")
    @classmethod
    def _clean(cls, value: str) -> str:
        value = " ".join(value.split()).strip()
        if not value:
            raise ValueError("question must not be blank")
        return value


class DemoCheck(BaseModel):
    check_id: str
    label: str
    passed: bool
    detail: str = ""


class DemoAskResponse(BaseModel):
    question_id: str
    question: str
    latency_ms: float
    budget_ms: int
    within_budget: bool
    status_matches_expectation: bool
    expected_status: str
    response: AskResponse
    checks: list[DemoCheck] = Field(default_factory=list)


class DemoRehearsalItem(BaseModel):
    question_id: str
    question: str
    answer_status: str
    confidence: str
    latency_ms: float
    budget_ms: int
    within_budget: bool
    passed: bool
    checks: list[DemoCheck] = Field(default_factory=list)


class DemoRehearsalResponse(BaseModel):
    passed: bool
    passed_count: int
    total: int
    budget_ms: int
    target_latency_ms: int
    questions: list[DemoRehearsalItem]


__all__ = [
    "DemoAskRequest",
    "DemoAskResponse",
    "DemoCheck",
    "DemoQuestion",
    "DemoRehearsalItem",
    "DemoRehearsalResponse",
    "DemoScriptResponse",
]
