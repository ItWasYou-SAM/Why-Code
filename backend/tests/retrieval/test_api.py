"""HTTP contracts: /ask plus the supporting evidence, history and feedback
endpoints. The API is transport only; the pipeline does the work."""

from __future__ import annotations

from fastapi.testclient import TestClient
from retrieval_fixtures import (
    REPO,
    FakeBackend,
    FakeEmbedder,
    FakeGenerator,
    FakeLookup,
    answer_json,
    candidate,
    context,
    review_and_commit,
)

from app.main import create_app
from app.retrieval.answer.answer_synthesizer import ANSWER_SCHEMA_NAME
from app.retrieval.config import RetrievalConfig
from app.retrieval.models import EntityType
from app.retrieval.question.entity_extractor import EntityIndex, KnownEntity
from app.retrieval.service import RetrievalService

INDEX = EntityIndex(
    entities=[
        KnownEntity(
            type=EntityType.CONFIG_KEY,
            identifier="retry.max_attempts",
            aliases=("retry limit", "retries"),
        )
    ]
)


def _service(*, with_lookup: bool = False, repo: str | None = REPO) -> RetrievalService:
    item = candidate("dec_001")
    ctx = context(item)
    lookup = None
    if with_lookup:
        lookup = FakeLookup(
            contexts={"dec_001": ctx},
            evidence_by_id={"ev_101": review_and_commit()[0]},
            history={"retry.max_attempts": [ctx]},
        )
    return RetrievalService(
        backend=FakeBackend(
            exact=[item],
            fulltext=[item],
            vector=[item],
            contexts={"dec_001": ctx},
        ),
        embedder=FakeEmbedder(),
        generator=FakeGenerator(
            {ANSWER_SCHEMA_NAME: answer_json(), "retrieval_entities": "{}"}
        ),
        config=RetrievalConfig(repo=repo, parallel_retrieval=False),
        index=INDEX,
        lookup=lookup,
    )


def _client(**kwargs) -> TestClient:
    return TestClient(create_app(retrieval_service=_service(**kwargs)))


def test_ask_returns_the_contract_shape() -> None:
    response = _client().post(
        "/ask",
        json={
            "question": "Why are retries disabled by default in httpx?",
            "repo": REPO,
            "max_evidence_items": 8,
            "max_answer_tokens": 700,
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer_status"] == "answered"
    assert body["confidence"] == "high"
    assert body["decision_ids"] == ["dec_001"]
    assert body["evidence"][0]["evidence_id"] == "ev_101"
    assert body["graph_path"]
    assert body["retrieval_trace"]["selected_decisions"] == 1
    required = {
        "answer_status",
        "answer",
        "confidence",
        "decision_ids",
        "evidence",
        "citations",
        "graph_path",
        "supersession",
        "caveats",
        "nearest_candidates",
        "retrieval_trace",
    }
    assert required <= set(body)


def test_ask_refusal_returns_the_contract_shape() -> None:
    service = RetrievalService(
        backend=FakeBackend(),
        embedder=FakeEmbedder(),
        generator=FakeGenerator({}),
        config=RetrievalConfig(repo=REPO, parallel_retrieval=False),
    )
    response = TestClient(create_app(retrieval_service=service)).post(
        "/ask",
        json={"question": "Why does httpx use a purple logo?", "repo": REPO},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer_status"] == "insufficient_evidence"
    assert body["confidence"] == "none"
    assert body["answer"] == (
        "I could not identify a relevant code entity or decision."
    )
    assert body["citations"] == []
    assert body["graph_path"] == []
    assert body["supersession"] == []
    assert body["decision_ids"] == []
    assert body["evidence"] == []
    assert body["retrieval_trace"]["selected_decisions"] == 0
    required = {
        "answer_status",
        "answer",
        "confidence",
        "decision_ids",
        "evidence",
        "citations",
        "graph_path",
        "supersession",
        "caveats",
        "nearest_candidates",
        "retrieval_trace",
    }
    assert required <= set(body)


def test_versioned_alias_is_available() -> None:
    response = _client().post(
        "/api/v1/ask",
        json={"question": "Why are retries disabled by default?", "repo": REPO},
    )
    assert response.status_code == 200
    assert response.json()["answer_status"] == "answered"


def test_invalid_request_is_422() -> None:
    client = _client()
    assert client.post("/ask", json={"question": "   ", "repo": REPO}).status_code == 422
    assert (
        client.post(
            "/ask",
            json={
                "question": "Why?",
                "repo": REPO,
                "max_evidence_items": 0,
            },
        ).status_code
        == 422
    )


def test_ask_without_a_configured_service_is_503() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/ask",
        json={"question": "Why are retries disabled?", "repo": REPO},
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"]


def test_missing_repo_is_400_when_the_service_has_no_default() -> None:
    response = _client(repo=None).post(
        "/ask", json={"question": "Why are retries disabled?"}
    )
    assert response.status_code == 400
    assert "repo is required" in response.json()["detail"]


def test_include_flags_are_transported() -> None:
    body = _client().post(
        "/ask",
        json={
            "question": "Why are retries disabled?",
            "repo": REPO,
            "include_evidence": False,
            "include_graph_path": False,
        },
    ).json()
    assert body["evidence"] == []
    assert body["graph_path"] == []


def test_decision_endpoint_returns_graph_context() -> None:
    body = _client(with_lookup=True).get("/decisions/dec_001").json()
    assert body["decision_id"] == "dec_001"
    assert body["claim"] == "HTTPX does not retry requests by default."
    assert body["status"] == "active"
    assert body["targets"]
    assert body["evidence"][0]["evidence_id"] == "ev_101"
    assert body["graph_path"][-2:] == ["Evidence:ev_101", "Evidence:ev_102"]


def test_decision_endpoint_404() -> None:
    response = _client(with_lookup=True).get("/decisions/nope")
    assert response.status_code == 404


def test_evidence_endpoint_returns_the_record() -> None:
    body = _client(with_lookup=True).get("/evidence/ev_101").json()
    assert body["evidence_id"] == "ev_101"
    assert body["kind"] == "review_comment"
    assert body["author"] == "alice"
    assert body["path"] == "httpx/_client.py"


def test_evidence_endpoint_404() -> None:
    assert _client(with_lookup=True).get("/evidence/nope").status_code == 404


def test_entity_history_endpoint() -> None:
    body = _client(with_lookup=True).get(
        "/entities/retry.max_attempts/history"
    ).json()
    assert body["entity_id"] == "retry.max_attempts"
    assert body["decisions"][0]["decision_id"] == "dec_001"


def test_feedback_is_recorded() -> None:
    client = _client()
    first = client.post(
        "/feedback",
        json={
            "question": "Why are retries disabled?",
            "verdict": "helpful",
            "decision_ids": ["dec_001"],
        },
    )
    second = client.post(
        "/feedback",
        json={
            "question": "Why are retries disabled?",
            "verdict": "missing_evidence",
            "comment": "Show the linked issue.",
        },
    )
    assert first.status_code == 200
    assert first.json() == {"feedback_id": "fb_1", "status": "recorded"}
    assert second.json()["feedback_id"] == "fb_2"


def test_feedback_rejects_unknown_verdict() -> None:
    response = _client().post(
        "/feedback",
        json={"question": "Why?", "verdict": "amazing"},
    )
    assert response.status_code == 422


def test_health_still_does_not_need_the_retrieval_service() -> None:
    client = TestClient(create_app())
    assert client.get("/health").status_code == 200
