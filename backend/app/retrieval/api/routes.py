"""Answer API routes. Mounted by ``app.main.create_app``."""

from __future__ import annotations

import threading

from fastapi import APIRouter, HTTPException, Query

from app.retrieval.api.schemas import (
    AskRequest,
    AskResponse,
    DecisionDetail,
    EntityHistoryResponse,
    EvidenceDetail,
    FeedbackRequest,
    FeedbackResponse,
)
from app.retrieval.models import NearestCandidate
from app.retrieval.service import RetrievalService


def build_router(service: RetrievalService | None) -> APIRouter:
    router = APIRouter(tags=["retrieval"])
    feedback: list[FeedbackRequest] = []
    lock = threading.Lock()

    def require() -> RetrievalService:
        if service is None:
            raise HTTPException(
                status_code=503,
                detail="retrieval service is not configured",
            )
        return service

    def repo_or_400(current: RetrievalService, repo: str | None) -> str:
        try:
            return current.resolve_repo(repo)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error

    @router.post("/ask", response_model=AskResponse)
    @router.post(
        "/api/v1/ask", response_model=AskResponse, include_in_schema=False
    )
    def ask(request: AskRequest) -> AskResponse:
        current = require()
        repo_or_400(current, request.repo)
        return current.ask(request.parameters())

    @router.get("/decisions/{decision_id}", response_model=DecisionDetail)
    @router.get(
        "/api/v1/decisions/{decision_id}",
        response_model=DecisionDetail,
        include_in_schema=False,
    )
    def decision(
        decision_id: str, repo: str | None = Query(default=None)
    ) -> DecisionDetail:
        current = require()
        resolved = repo_or_400(current, repo)
        context = current.get_decision(decision_id, repo=resolved)
        if context is None:
            raise HTTPException(status_code=404, detail="decision not found")
        return DecisionDetail.from_context(context)

    @router.get("/evidence/{evidence_id}", response_model=EvidenceDetail)
    @router.get(
        "/api/v1/evidence/{evidence_id}",
        response_model=EvidenceDetail,
        include_in_schema=False,
    )
    def evidence(
        evidence_id: str, repo: str | None = Query(default=None)
    ) -> EvidenceDetail:
        current = require()
        resolved = repo_or_400(current, repo)
        item = current.get_evidence(evidence_id, repo=resolved)
        if item is None:
            raise HTTPException(status_code=404, detail="evidence not found")
        return EvidenceDetail(
            evidence_id=item.evidence_id,
            kind=item.kind,
            url=item.url,
            excerpt=item.excerpt,
            author=item.author,
            created_at=item.created_at,
            pr_number=item.pr_number,
            issue_number=item.issue_number,
            commit_sha=item.commit_sha,
            path=item.path,
            start_line=item.start_line,
            end_line=item.end_line,
        )

    @router.get(
        "/entities/{entity_id:path}/history", response_model=EntityHistoryResponse
    )
    @router.get(
        "/api/v1/entities/{entity_id:path}/history",
        response_model=EntityHistoryResponse,
        include_in_schema=False,
    )
    def entity_history(
        entity_id: str, repo: str | None = Query(default=None)
    ) -> EntityHistoryResponse:
        current = require()
        resolved = repo_or_400(current, repo)
        contexts = current.entity_history(entity_id, repo=resolved)
        return EntityHistoryResponse(
            entity_id=entity_id,
            decisions=[
                NearestCandidate(
                    decision_id=context.decision.decision_id,
                    claim=context.decision.claim,
                    status=context.decision.status,
                )
                for context in contexts
            ],
        )

    @router.post("/feedback", response_model=FeedbackResponse)
    @router.post(
        "/api/v1/feedback", response_model=FeedbackResponse, include_in_schema=False
    )
    def submit_feedback(request: FeedbackRequest) -> FeedbackResponse:
        with lock:
            feedback.append(request)
            number = len(feedback)
        return FeedbackResponse(feedback_id=f"fb_{number}")

    # Retained for the demo UI's "what did the judge say?" panel.
    return router


__all__ = ["build_router"]
