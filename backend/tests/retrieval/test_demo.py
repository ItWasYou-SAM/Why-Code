"""Judge-demo contracts.

These tests exercise the real pipeline against the prepared corpus: question
understanding, four retrieval sources, fusion, reranking, packing, deterministic
synthesis and citation validation. They are also the regression suite for the
five statements the live demo makes.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import create_demo_app
from app.retrieval.answer.refusal_handler import NO_ENTITY_MESSAGE
from app.retrieval.demo import create_demo_service
from app.retrieval.demo.data import (
    CONNECTION_ONLY,
    DEFAULT_OFF,
    DEFAULT_ON,
    DEMO_REPO,
    QUESTION_1,
    QUESTION_2,
    QUESTION_3,
    QUESTION_4,
    QUESTION_5,
    TIMEOUT_PHASES,
)
from app.retrieval.models import (
    AnswerStatus,
    AskParameters,
    DecisionState,
    EvidenceStrength,
    RefusalMode,
)
from app.retrieval.service import RetrievalService

_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"“])")


@pytest.fixture(scope="module")
def service() -> RetrievalService:
    return create_demo_service()


@pytest.fixture(scope="module")
def client(service: RetrievalService) -> Iterator[TestClient]:
    app = create_demo_app()
    with TestClient(app) as test_client:
        yield test_client


def ask(service: RetrievalService, question: str):
    return service.ask(AskParameters(question=question, repo=DEMO_REPO))


def _assert_cited(answer: str) -> None:
    sentences = [part.strip() for part in _SENTENCE.split(answer) if part.strip()]
    assert sentences
    for sentence in sentences:
        assert re.search(r"\[E\d+\]", sentence), sentence


def test_script_is_exactly_the_rehearsed_five(client: TestClient) -> None:
    response = client.get("/demo/script")
    assert response.status_code == 200
    payload = response.json()
    assert payload["repo"] == DEMO_REPO
    assert payload["latency_budget_ms"] == 8000
    assert payload["target_latency_ms"] == 5000
    assert [item["question"] for item in payload["questions"]] == [
        QUESTION_1,
        QUESTION_2,
        QUESTION_3,
        QUESTION_4,
        QUESTION_5,
    ]
    assert payload["questions"][-1]["expected_status"] == "insufficient_evidence"
    assert payload["questions"][-1]["expected_refusal_mode"] == "NO_ENTITY"
    assert client.get("/api/v1/demo/script").json() == payload


def test_q1_is_cited_config_decision_with_people_and_path(
    service: RetrievalService,
) -> None:
    response = ask(service, QUESTION_1)
    assert response.answer_status is AnswerStatus.ANSWERED
    assert response.citation_valid is True
    assert response.evidence_strength is EvidenceStrength.STRONG
    assert response.decisions
    assert response.decisions[0].decision_id == DEFAULT_OFF
    assert response.decisions[0].is_historical is False
    assert response.decision_ids == [DEFAULT_OFF]
    assert response.evidence
    evidence_ids = {item.evidence_id for item in response.evidence}
    assert set(response.citations) <= evidence_ids
    cited_aliases = {
        item.alias
        for item in response.evidence
        if item.evidence_id in response.citations
    }
    assert all(
        alias is not None and f"[{alias}]" in response.answer
        for alias in cited_aliases
    )
    assert {person.login for person in response.people} >= {
        "tomchristie",
        "lukasa",
    }
    assert any(node.startswith("ConfigKey:") for node in response.graph_path)
    assert any(node.startswith("Decision:") for node in response.graph_path)
    assert any(node.startswith("Evidence:") for node in response.graph_path)
    _assert_cited(response.answer)
    assert "PR #1525" in response.answer


def test_q2_is_connection_only_with_code_rationale(
    service: RetrievalService,
) -> None:
    response = ask(service, QUESTION_2)
    assert response.answer_status is AnswerStatus.ANSWERED
    assert response.citation_valid is True
    assert CONNECTION_ONLY in response.decision_ids
    assert response.decisions[0].decision_id == CONNECTION_ONLY
    assert any(
        item.path == "httpx/_client.py" for item in response.evidence
    )
    assert "httpx/_client.py" in response.answer
    assert "connection" in response.answer.casefold()
    assert "idempotent" in response.answer.casefold()
    _assert_cited(response.answer)


def test_q3_labels_the_reversal_historical(
    service: RetrievalService,
) -> None:
    response = ask(service, QUESTION_3)
    assert response.answer_status is AnswerStatus.ANSWERED
    assert response.citation_valid is True
    assert DEFAULT_ON in response.decision_ids
    assert DEFAULT_OFF in response.decision_ids
    historical = [
        decision for decision in response.decisions if decision.is_historical
    ]
    assert [decision.decision_id for decision in historical] == [DEFAULT_ON]
    assert historical[0].status is DecisionState.REVERSED
    assert historical[0].superseded_by == DEFAULT_OFF
    assert any(
        record.decision_id == DEFAULT_ON
        and record.superseded_by == DEFAULT_OFF
        for record in response.supersession
    )
    current = [
        decision for decision in response.decisions if not decision.is_historical
    ]
    assert any(decision.decision_id == DEFAULT_OFF for decision in current)
    assert "PR #1200" in response.answer
    assert "PR #1525" in response.answer
    assert "reversed" in response.answer.casefold()
    _assert_cited(response.answer)


def test_q4_follows_a_different_config_key(
    service: RetrievalService,
) -> None:
    response = ask(service, QUESTION_4)
    assert response.answer_status is AnswerStatus.ANSWERED
    assert response.citation_valid is True
    assert response.decisions[0].decision_id == TIMEOUT_PHASES
    assert any(
        node == "ConfigKey:timeout" for node in response.graph_path
    )
    assert "httpx/_config.py" in response.answer
    assert "pool" in response.answer.casefold()
    _assert_cited(response.answer)


def test_q5_refuses_without_calling_synthesis(
    service: RetrievalService, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(config):  # pragma: no cover - called only on a regression
        raise AssertionError("the answer synthesizer must not run for q5")

    monkeypatch.setattr(service, "synthesizer_factory", explode)
    response = ask(service, QUESTION_5)
    assert response.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.refusal_mode is RefusalMode.NO_ENTITY
    assert response.answer == NO_ENTITY_MESSAGE
    assert response.evidence == []
    assert response.citations == []
    assert response.decisions == []
    assert response.evidence_strength is EvidenceStrength.NONE
    assert response.graph_path == []


def test_same_question_returns_a_stable_structure(
    service: RetrievalService,
) -> None:
    for question in (QUESTION_1, QUESTION_2, QUESTION_3, QUESTION_4, QUESTION_5):
        first = ask(service, question).model_dump(mode="json")
        second = ask(service, question).model_dump(mode="json")
        assert first == second


def test_ask_route_runs_all_checks_for_script(
    client: TestClient,
) -> None:
    for item in client.get("/demo/script").json()["questions"]:
        response = client.post(
            "/demo/ask", json={"question": item["question"]}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["status_matches_expectation"] is True
        assert payload["within_budget"] is True
        assert all(check["passed"] for check in payload["checks"]), payload


def test_rehearsal_passes_and_stays_under_budget(
    client: TestClient,
) -> None:
    response = client.post("/demo/rehearse")
    assert response.status_code == 200
    payload = response.json()
    assert payload["passed"] is True
    assert payload["passed_count"] == payload["total"] == 5
    assert all(item["within_budget"] for item in payload["questions"])
    assert all(item["latency_ms"] < 8000 for item in payload["questions"])
    assert any(
        check["check_id"] == "historical" and check["passed"]
        for question in payload["questions"]
        for check in question["checks"]
    )


def test_off_script_questions_and_other_repos_are_rejected(
    client: TestClient,
) -> None:
    assert (
        client.post(
            "/demo/ask", json={"question": "Why is the sky blue?"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/demo/ask",
            json={"question": QUESTION_1, "repo": "other/repo"},
        ).status_code
        == 400
    )


def test_demo_cors_allows_the_frontend_origin(
    client: TestClient,
) -> None:
    response = client.options(
        "/demo/ask",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        "http://localhost:3000"
    )
