"""End-to-end pipeline contracts with fakes: retrieval, fusion, graph
expansion, packing, synthesis, validation and refusal."""

from __future__ import annotations

import pytest
from retrieval_fixtures import (
    REPO,
    FakeBackend,
    FakeEmbedder,
    FakeGenerator,
    FakeLookup,
    answer_json,
    candidate,
    context,
    evidence,
    review_and_commit,
)

from app.retrieval.answer.answer_synthesizer import ANSWER_SCHEMA_NAME
from app.retrieval.config import RetrievalConfig
from app.retrieval.models import (
    AnswerConfidence,
    AnswerStatus,
    AskParameters,
    DecisionState,
    EntityType,
    QuestionType,
)
from app.retrieval.question.entity_extractor import EntityIndex, KnownEntity
from app.retrieval.service import DEFAULT_CAVEAT, RetrievalService

INDEX = EntityIndex(
    entities=[
        KnownEntity(
            type=EntityType.CONFIG_KEY,
            identifier="retry.max_attempts",
            aliases=("retry limit", "retries", "retry"),
        ),
        KnownEntity(
            type=EntityType.SYMBOL,
            identifier="Client.send",
            aliases=("client send",),
        ),
        KnownEntity(
            type=EntityType.CONFIG_KEY,
            identifier="timeout",
            aliases=("timeouts", "pool timeout"),
        ),
    ]
)


def _service(
    *,
    backend: FakeBackend | None = None,
    generator: FakeGenerator | None = None,
    config: RetrievalConfig | None = None,
    lookup: FakeLookup | None = None,
) -> RetrievalService:
    return RetrievalService(
        backend=backend or _healthy_backend(),
        embedder=FakeEmbedder(),
        generator=generator
        or FakeGenerator({ANSWER_SCHEMA_NAME: answer_json(), "retrieval_entities": "{}"}),
        config=config
        or RetrievalConfig(repo=REPO, parallel_retrieval=False),
        index=INDEX,
        lookup=lookup,
    )


def _healthy_backend(
    item=None,
    *,
    evidence_items=None,
    later_decisions=None,
) -> FakeBackend:
    decision = item or candidate("dec_001")
    ctx = context(
        decision,
        evidence_items=evidence_items,
        later_decisions=later_decisions,
    )
    return FakeBackend(
        exact=[decision],
        fulltext=[decision],
        vector=[decision],
        contexts={"dec_001": ctx},
    )


def test_happy_path_returns_an_evidence_backed_answer() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: answer_json(
                answer=(
                    "HTTPX disables retries by default [E1]. "
                    "Automatic retries can duplicate requests [E2]."
                )
            )
        }
    )
    service = _service(generator=generator)
    response = service.ask(
        AskParameters(
            question="Why are retries disabled by default in httpx?",
            repo=REPO,
        )
    )
    assert response.answer_status is AnswerStatus.ANSWERED
    assert response.confidence is AnswerConfidence.HIGH
    assert response.decision_ids == ["dec_001"]
    assert [item.evidence_id for item in response.evidence] == ["ev_101", "ev_102"]
    assert response.citations == ["ev_101", "ev_102"]
    assert DEFAULT_CAVEAT in response.caveats
    assert response.graph_path[0].startswith("ConfigKey:")
    assert "Decision:dec_001" in response.graph_path
    assert all(item.startswith("Evidence:") for item in response.graph_path[-2:])


def test_retrieval_trace_reports_every_stage() -> None:
    service = _service()
    response = service.ask(
        AskParameters(question="Why are retries disabled by default?", repo=REPO)
    )
    trace = response.retrieval_trace
    assert trace.exact_matches == 1
    assert trace.fulltext_candidates == 1
    assert trace.vector_candidates == 1
    assert trace.graph_candidates == 1
    assert trace.fused_candidates == 1
    assert trace.selected_decisions == 1


def test_all_three_retrievers_and_graph_expansion_are_called() -> None:
    backend = _healthy_backend()
    service = _service(backend=backend)
    service.ask(
        AskParameters(question="Why are retries disabled by default?", repo=REPO)
    )
    assert set(backend.names()) >= {"exact", "fulltext", "vector", "expand"}


def test_same_question_returns_a_stable_structure() -> None:
    generator = FakeGenerator(
        {ANSWER_SCHEMA_NAME: answer_json(
            answer="HTTPX disables retries by default [E1]. "
            "Automatic retries can duplicate requests [E2]."
        )},
        repeat=True,
    )
    service = _service(generator=generator)
    parameters = AskParameters(
        question="Why are retries disabled by default?", repo=REPO
    )
    first = service.ask(parameters)
    second = service.ask(parameters)
    assert first == second


def test_max_evidence_items_is_honored_end_to_end() -> None:
    service = _service(
        generator=FakeGenerator(
            {
                ANSWER_SCHEMA_NAME: answer_json(
                    answer="HTTPX disables retries by default [E1].",
                    citations=("E1",),
                )
            }
        )
    )
    response = service.ask(
        AskParameters(
            question="Why are retries disabled by default?",
            repo=REPO,
            max_evidence_items=1,
        )
    )
    assert response.answer_status is AnswerStatus.ANSWERED
    assert len(response.evidence) == 1
    assert response.evidence[0].evidence_id == "ev_101"
    assert response.confidence is AnswerConfidence.MEDIUM


def test_include_flags_remove_evidence_and_graph_path() -> None:
    response = _service().ask(
        AskParameters(
            question="Why are retries disabled by default?",
            repo=REPO,
            include_evidence=False,
            include_graph_path=False,
        )
    )
    assert response.evidence == []
    assert response.graph_path == []
    assert response.answer_status is AnswerStatus.ANSWERED


def test_no_entity_refuses_without_calling_the_model() -> None:
    generator = FakeGenerator({})
    backend = FakeBackend()
    service = _service(backend=backend, generator=generator)
    response = service.ask(
        AskParameters(question="Why does httpx use a purple logo?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.confidence is AnswerConfidence.NONE
    assert response.answer == "I could not identify a relevant code entity or decision."
    assert response.decision_ids == []
    assert response.evidence == []
    assert response.nearest_candidates == []
    assert response.retrieval_trace.selected_decisions == 0
    assert generator.calls == []


def test_entity_without_a_decision_uses_the_no_decision_refusal() -> None:
    generator = FakeGenerator({})
    service = _service(backend=FakeBackend(), generator=generator)
    response = service.ask(
        AskParameters(question="Why is the retry limit three?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.answer == (
        "The graph contains the code entity but no defensible decision trail."
    )
    assert generator.calls == []


def test_candidate_without_evidence_uses_no_decision_refusal() -> None:
    item = candidate("dec_001")
    backend = FakeBackend(
        exact=[item],
        fulltext=[],
        vector=[],
        contexts={"dec_001": context(item, evidence_items=[])},
    )
    generator = FakeGenerator({})
    response = _service(backend=backend, generator=generator).ask(
        AskParameters(question="Why are retries disabled?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.answer == (
        "The graph contains the code entity but no defensible decision trail."
    )
    assert generator.calls == []


def test_superseded_only_refuses_and_labels_history() -> None:
    item = candidate("dec_001", status=DecisionState.SUPERSEDED)
    backend = _healthy_backend(item, later_decisions=["dec_009"])
    generator = FakeGenerator({})
    response = _service(backend=backend, generator=generator).ask(
        AskParameters(question="Why are retries disabled?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.confidence is AnswerConfidence.NONE
    assert "superseded or reversed" in response.answer
    assert response.decision_ids == ["dec_001"]
    assert response.nearest_candidates[0].status is DecisionState.SUPERSEDED
    assert generator.calls == []


def test_conflicting_decisions_return_contested_with_both_sides() -> None:
    first = candidate(
        "dec_001",
        claim="Retries are disabled by default.",
        status=DecisionState.CONTESTED,
    )
    second = candidate(
        "dec_002",
        claim="Retries are required for this client.",
        status=DecisionState.CONTESTED,
    )
    backend = FakeBackend(
        exact=[first],
        fulltext=[second],
        vector=[],
        contexts={
            "dec_001": context(
                first,
                evidence_items=[evidence("ev_1", decision_id="dec_001")],
            ),
            "dec_002": context(
                second,
                evidence_items=[
                    evidence(
                        "ev_2",
                        decision_id="dec_002",
                        excerpt="Retries are required for this client.",
                    )
                ],
            ),
        },
    )
    generator = FakeGenerator({})
    response = _service(backend=backend, generator=generator).ask(
        AskParameters(question="Why are retries disabled?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.CONTESTED
    assert response.confidence is AnswerConfidence.LOW
    assert "Retries are disabled by default." in response.answer
    assert "Retries are required for this client." in response.answer
    assert "[E1]" in response.answer
    assert "[E2]" in response.answer
    assert response.decision_ids == ["dec_001", "dec_002"]
    assert response.graph_path
    assert generator.calls == []


def test_invalid_citation_is_regenerated_once_then_accepted() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: [
                answer_json(
                    answer="HTTPX disables retries by default [E9].",
                    citations=("E9",),
                ),
                answer_json(
                    answer="HTTPX disables retries by default [E1].",
                    citations=("E1",),
                ),
            ]
        }
    )
    response = _service(generator=generator).ask(
        AskParameters(question="Why are retries disabled by default?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.ANSWERED
    assert len(generator.calls) == 2
    assert "STRICT CORRECTION" in generator.calls[1].user


def test_two_invalid_answers_are_refused() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: [
                answer_json(
                    answer="HTTPX disables retries by default [E9].",
                    citations=("E9",),
                ),
                answer_json(
                    answer="HTTPX disables retries by default [E8].",
                    citations=("E8",),
                ),
            ]
        }
    )
    response = _service(generator=generator).ask(
        AskParameters(question="Why are retries disabled by default?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.evidence == []
    assert response.nearest_candidates[0].decision_id == "dec_001"
    assert any("citation validation" in caveat for caveat in response.caveats)
    assert len(generator.calls) == 2


def test_uncited_answer_is_refused() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: [
                answer_json(
                    answer="HTTPX disables retries by default.",
                    citations=(),
                ),
                answer_json(
                    answer="HTTPX disables retries by default.",
                    citations=(),
                ),
            ]
        }
    )
    response = _service(generator=generator).ask(
        AskParameters(question="Why are retries disabled by default?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert any("validation failed" in caveat.lower() for caveat in response.caveats)


def test_model_declared_insufficient_is_refused() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: answer_json(
                status="insufficient_evidence",
                answer="INSUFFICIENT_EVIDENCE",
                citations=(),
                confidence="none",
            )
        }
    )
    response = _service(generator=generator).ask(
        AskParameters(question="Why are retries disabled by default?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.confidence is AnswerConfidence.NONE
    assert response.evidence == []


def test_answered_with_no_confidence_fails_closed() -> None:
    generator = FakeGenerator(
        {
            ANSWER_SCHEMA_NAME: answer_json(
                answer="HTTPX disables retries by default [E1].",
                citations=("E1",),
                confidence="none",
            )
        }
    )
    response = _service(generator=generator).ask(
        AskParameters(question="Why are retries disabled by default?", repo=REPO)
    )
    assert response.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert response.confidence is AnswerConfidence.NONE
    assert "no confidence" in response.caveats[0]


def test_at_most_five_decisions_are_selected() -> None:
    decisions = [candidate(f"dec_{number}") for number in range(8)]
    contexts = {
        f"dec_{number}": context(
            item,
            evidence_items=[
                evidence(f"ev_{number}", decision_id=f"dec_{number}")
            ],
        )
        for number, item in enumerate(decisions)
    }
    backend = FakeBackend(exact=decisions, contexts=contexts)
    response = _service(backend=backend).ask(
        AskParameters(question="Why are retries disabled?", repo=REPO)
    )
    assert response.retrieval_trace.fused_candidates == 8
    assert response.retrieval_trace.selected_decisions == 5
    assert len(response.decision_ids) == 5


def test_repo_is_required() -> None:
    service = RetrievalService(
        backend=_healthy_backend(),
        embedder=FakeEmbedder(),
        generator=FakeGenerator({}),
        config=RetrievalConfig(parallel_retrieval=False),
        index=INDEX,
    )
    with pytest.raises(ValueError, match="repo is required"):
        service.ask(AskParameters(question="Why are retries disabled?"))


def test_lookup_endpoints_delegate_to_the_lookup_backend() -> None:
    item = candidate("dec_001")
    ctx = context(item)
    lookup = FakeLookup(
        contexts={"dec_001": ctx},
        evidence_by_id={"ev_101": review_and_commit()[0]},
        history={"retry.max_attempts": [ctx]},
    )
    service = _service(lookup=lookup)
    assert service.get_decision("dec_001") == ctx
    assert service.get_evidence("ev_101") is not None
    assert service.entity_history("retry.max_attempts") == [ctx]


def test_lookup_without_backend_returns_empty_results() -> None:
    service = _service()
    assert service.get_decision("dec_001") is None
    assert service.get_evidence("ev_101") is None
    assert service.entity_history("retry.max_attempts") == []


def test_parallel_retrieval_produces_the_same_answer() -> None:
    parameters = AskParameters(
        question="Why are retries disabled by default?", repo=REPO
    )
    sequential = _service(
        config=RetrievalConfig(repo=REPO, parallel_retrieval=False),
        generator=FakeGenerator({ANSWER_SCHEMA_NAME: answer_json()}),
    )
    parallel = _service(
        config=RetrievalConfig(repo=REPO, parallel_retrieval=True),
        generator=FakeGenerator({ANSWER_SCHEMA_NAME: answer_json()}),
    )
    assert sequential.ask(parameters) == parallel.ask(parameters)


def test_question_type_is_not_sent_to_the_model() -> None:
    generator = FakeGenerator({ANSWER_SCHEMA_NAME: answer_json()})
    _service(generator=generator).ask(
        AskParameters(question="Why are retries disabled by default?", repo=REPO)
    )
    assert "Which of the following" not in generator.calls[0].user
    assert QuestionType.CONFIG_WHY.value not in generator.calls[0].user
