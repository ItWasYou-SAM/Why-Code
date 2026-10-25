"""The judge-facing demo flow.

These routes are a thin, rehearsed wrapper around the same ``/ask`` pipeline.
They add three things the judge experience needs: the fixed script, a
per-question expectation check, and an honest latency measurement. They never
accept an arbitrary question, so the live demo cannot drift off-script.
"""

from __future__ import annotations

from time import perf_counter

from fastapi import APIRouter, HTTPException

from app.retrieval.api.demo_schemas import (
    DemoAskRequest,
    DemoAskResponse,
    DemoCheck,
    DemoQuestion,
    DemoRehearsalItem,
    DemoRehearsalResponse,
    DemoScriptResponse,
)
from app.retrieval.demo.data import (
    DEMO_REPO,
    DEMO_SCRIPT,
    DemoQuestionSpec,
)
from app.retrieval.models import AnswerStatus, AskParameters, AskResponse, EvidenceStrength
from app.retrieval.service import RetrievalService

DEMO_LATENCY_BUDGET_MS = 8000
DEMO_TARGET_LATENCY_MS = 5000

_BY_FOLD = {spec.question.casefold(): spec for spec in DEMO_SCRIPT}


def build_demo_router(service: RetrievalService | None) -> APIRouter:
    router = APIRouter(tags=["demo"])

    def require() -> RetrievalService:
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="demo service is not configured",
            )
        return service

    def spec_for(question: str) -> DemoQuestionSpec:
        spec = _BY_FOLD.get(" ".join(question.split()).casefold())
        if spec is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "question is not part of the rehearsed demo script; "
                    "use GET /demo/script"
                ),
            )
        return spec

    def run(question: str) -> tuple[DemoQuestionSpec, AskResponse, float]:
        current = require()
        spec = spec_for(question)
        started = perf_counter()
        response = current.ask(
            AskParameters(
                question=spec.question,
                repo=DEMO_REPO,
                max_evidence_items=8,
                max_answer_tokens=700,
                include_graph_path=True,
                include_evidence=True,
            )
        )
        latency_ms = (perf_counter() - started) * 1000.0
        return spec, response, latency_ms

    @router.get("/demo/script", response_model=DemoScriptResponse)
    @router.get(
        "/api/v1/demo/script",
        response_model=DemoScriptResponse,
        include_in_schema=False,
    )
    def script() -> DemoScriptResponse:
        return DemoScriptResponse(
            repo=DEMO_REPO,
            latency_budget_ms=DEMO_LATENCY_BUDGET_MS,
            target_latency_ms=DEMO_TARGET_LATENCY_MS,
            questions=[
                DemoQuestion(
                    question_id=spec.question_id,
                    question=spec.question,
                    focus=spec.focus,
                    expectation=spec.expectation,
                    expected_status=spec.expected_status.value,
                    expected_decision_id=spec.expected_decision_id,
                    expected_refusal_mode=(
                        spec.expected_refusal_mode.value
                        if spec.expected_refusal_mode is not None
                        else None
                    ),
                )
                for spec in DEMO_SCRIPT
            ],
        )

    @router.post("/demo/ask", response_model=DemoAskResponse)
    @router.post(
        "/api/v1/demo/ask",
        response_model=DemoAskResponse,
        include_in_schema=False,
    )
    def ask(request: DemoAskRequest) -> DemoAskResponse:
        if request.repo and request.repo != DEMO_REPO:
            raise HTTPException(
                status_code=400,
                detail=f"the prepared demo is scoped to {DEMO_REPO}",
            )
        spec, response, latency_ms = run(request.question)
        checks = _checks(
            spec, response, latency_ms, DEMO_LATENCY_BUDGET_MS
        )
        return DemoAskResponse(
            question_id=spec.question_id,
            question=spec.question,
            latency_ms=round(latency_ms, 3),
            budget_ms=DEMO_LATENCY_BUDGET_MS,
            within_budget=latency_ms <= DEMO_LATENCY_BUDGET_MS,
            status_matches_expectation=response.answer_status is spec.expected_status,
            expected_status=spec.expected_status.value,
            response=response,
            checks=checks,
        )

    @router.post("/demo/rehearse", response_model=DemoRehearsalResponse)
    @router.post(
        "/api/v1/demo/rehearse",
        response_model=DemoRehearsalResponse,
        include_in_schema=False,
    )
    def rehearse() -> DemoRehearsalResponse:
        items: list[DemoRehearsalItem] = []
        for spec in DEMO_SCRIPT:
            _, response, latency_ms = run(spec.question)
            checks = _checks(
                spec, response, latency_ms, DEMO_LATENCY_BUDGET_MS
            )
            items.append(
                DemoRehearsalItem(
                    question_id=spec.question_id,
                    question=spec.question,
                    answer_status=response.answer_status.value,
                    confidence=response.confidence.value,
                    latency_ms=round(latency_ms, 3),
                    budget_ms=DEMO_LATENCY_BUDGET_MS,
                    within_budget=latency_ms <= DEMO_LATENCY_BUDGET_MS,
                    passed=all(check.passed for check in checks),
                    checks=checks,
                )
            )
        passed_count = sum(1 for item in items if item.passed)
        return DemoRehearsalResponse(
            passed=passed_count == len(items),
            passed_count=passed_count,
            total=len(items),
            budget_ms=DEMO_LATENCY_BUDGET_MS,
            target_latency_ms=DEMO_TARGET_LATENCY_MS,
            questions=items,
        )

    return router


def _checks(
    spec: DemoQuestionSpec,
    response: AskResponse,
    latency_ms: float,
    budget_ms: int,
) -> list[DemoCheck]:
    checks = [
        DemoCheck(
            check_id="status",
            label="Expected answer status",
            passed=response.answer_status is spec.expected_status,
            detail=(
                f"{response.answer_status.value} "
                f"(expected {spec.expected_status.value})"
            ),
        )
    ]

    if spec.expected_status is AnswerStatus.ANSWERED:
        checks.extend(
            [
                DemoCheck(
                    check_id="citations",
                    label="Citation gate passed",
                    passed=response.citation_valid,
                    detail=(
                        f"{len(response.citations)} resolved citation(s)"
                        if response.citation_valid
                        else "citation validation failed"
                    ),
                ),
                DemoCheck(
                    check_id="evidence",
                    label="Evidence attached",
                    passed=bool(response.evidence),
                    detail=f"{len(response.evidence)} evidence item(s)",
                ),
                DemoCheck(
                    check_id="decision",
                    label="Expected decision selected",
                    passed=(
                        spec.expected_decision_id is not None
                        and spec.expected_decision_id in response.decision_ids
                    ),
                    detail=(
                        spec.expected_decision_id or "no expected decision"
                    ),
                ),
                DemoCheck(
                    check_id="graph_path",
                    label="Graph path reaches evidence",
                    passed=(
                        any(node.startswith("Decision:") for node in response.graph_path)
                        and any(
                            node.startswith("Evidence:") for node in response.graph_path
                        )
                    ),
                    detail=f"{len(response.graph_path)} path node(s)",
                ),
                DemoCheck(
                    check_id="people",
                    label="People attached",
                    passed=bool(response.people),
                    detail=f"{len(response.people)} contributor(s)",
                ),
                DemoCheck(
                    check_id="strength",
                    label="Evidence strength is not thin",
                    passed=response.evidence_strength
                    in {EvidenceStrength.STRONG, EvidenceStrength.MODERATE},
                    detail=response.evidence_strength.value,
                ),
            ]
        )
    else:
        checks.extend(
            [
                DemoCheck(
                    check_id="refusal_mode",
                    label="Refusal mode matches",
                    passed=(
                        spec.expected_refusal_mode is not None
                        and response.refusal_mode is spec.expected_refusal_mode
                    ),
                    detail=(
                        response.refusal_mode.value
                        if response.refusal_mode is not None
                        else "no refusal mode"
                    ),
                ),
                DemoCheck(
                    check_id="refusal_bounded",
                    label="No evidence or citations on refusal",
                    passed=not response.evidence and not response.citations,
                    detail=(
                        f"{len(response.evidence)} evidence item(s), "
                        f"{len(response.citations)} citation(s)"
                    ),
                ),
            ]
        )

    if spec.expects_historical:
        checks.append(
            DemoCheck(
                check_id="historical",
                label="Superseded decision labelled historical",
                passed=any(decision.is_historical for decision in response.decisions),
                detail=f"{len(response.decisions)} decision(s) shown",
            )
        )

    checks.append(
        DemoCheck(
            check_id="latency",
            label="Prepared latency within budget",
            passed=latency_ms <= budget_ms,
            detail=f"{latency_ms:.1f} ms / {budget_ms} ms",
        )
    )
    return checks


__all__ = [
    "DEMO_LATENCY_BUDGET_MS",
    "DEMO_TARGET_LATENCY_MS",
    "build_demo_router",
]
