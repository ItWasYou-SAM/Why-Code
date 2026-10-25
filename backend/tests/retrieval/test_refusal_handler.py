"""Refusal modes are distinct and explicit. A judge must be able to tell
"no entity" from "entity but no decision" from "only reversed history"."""

from __future__ import annotations

from retrieval_fixtures import candidate, context, evidence, ranked, understanding

from app.retrieval.answer.refusal_handler import (
    CONTESTED_MESSAGE,
    NO_DECISION_MESSAGE,
    NO_ENTITY_MESSAGE,
    SUPERSEDED_MESSAGE,
    VALIDATION_MESSAGE,
    RefusalHandler,
)
from app.retrieval.context.evidence_packer import EvidencePacker
from app.retrieval.models import (
    AnswerConfidence,
    AnswerStatus,
    DecisionState,
    RefusalMode,
)


def test_no_entity_when_nothing_was_extracted_or_retrieved() -> None:
    result = RefusalHandler().decide(
        understanding=understanding(entities=[]),
        candidates=[],
        contexts={},
    )
    assert result is not None
    assert result.mode is RefusalMode.NO_ENTITY
    assert result.answer == NO_ENTITY_MESSAGE
    assert result.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.confidence is AnswerConfidence.NONE


def test_no_decision_when_an_entity_exists_but_nothing_was_retrieved() -> None:
    result = RefusalHandler().decide(
        understanding=understanding(),
        candidates=[],
        contexts={},
    )
    assert result is not None
    assert result.mode is RefusalMode.NO_DECISION
    assert result.answer == NO_DECISION_MESSAGE


def test_candidates_without_graph_evidence_are_no_decision() -> None:
    item = candidate("dec_001")
    result = RefusalHandler().decide(
        understanding=understanding(),
        candidates=[item],
        contexts={"dec_001": context(item, evidence_items=[])},
    )
    assert result is not None
    assert result.mode is RefusalMode.NO_DECISION
    assert result.answer == NO_DECISION_MESSAGE


def test_missing_context_is_no_decision() -> None:
    result = RefusalHandler().decide(
        understanding=understanding(),
        candidates=[candidate("dec_001")],
        contexts={},
    )
    assert result is not None
    assert result.mode is RefusalMode.NO_DECISION


def test_all_superseded_decisions_refuse_with_history_explanation() -> None:
    item = candidate("dec_001", status=DecisionState.SUPERSEDED)
    result = RefusalHandler().decide(
        understanding=understanding(),
        candidates=[item],
        contexts={"dec_001": context(item, later_decisions=["dec_009"])},
    )
    assert result is not None
    assert result.mode is RefusalMode.SUPERSEDED_ONLY
    assert result.answer.startswith(SUPERSEDED_MESSAGE)
    assert "Historical decision dec_001" in result.answer
    assert "dec_009" in result.answer
    assert result.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.confidence is AnswerConfidence.NONE
    assert result.decision_ids == ["dec_001"]
    assert result.nearest_candidates[0].decision_id == "dec_001"
    assert result.caveats


def test_all_reversed_decisions_are_also_superseded_only() -> None:
    item = candidate("dec_001", status=DecisionState.REVERSED)
    result = RefusalHandler().decide(
        understanding=understanding(),
        candidates=[item],
        contexts={"dec_001": context(item)},
    )
    assert result is not None
    assert result.mode is RefusalMode.SUPERSEDED_ONLY


def test_later_decision_alone_marks_history_as_not_current() -> None:
    item = candidate("dec_001")
    result = RefusalHandler().decide(
        understanding=understanding(),
        candidates=[item],
        contexts={"dec_001": context(item, later_decisions=["dec_009"])},
    )
    assert result is not None
    assert result.mode is RefusalMode.SUPERSEDED_ONLY


def test_reversed_without_a_later_id_still_explains_history() -> None:
    item = candidate("dec_001", status=DecisionState.REVERSED)
    result = RefusalHandler().decide(
        understanding=understanding(),
        candidates=[item],
        contexts={"dec_001": context(item)},
    )
    assert result is not None
    assert result.mode is RefusalMode.SUPERSEDED_ONLY
    assert "Historical decision dec_001" in result.answer
    assert "was later superseded or reversed" in result.answer
    assert result.caveats


def test_superseding_ids_are_deduplicated() -> None:
    item = candidate("dec_001", status=DecisionState.SUPERSEDED)
    item = item.model_copy(update={"superseded_by": "dec_009"})
    result = RefusalHandler().superseded_only(
        candidates=[item],
        contexts={
            "dec_001": context(item, later_decisions=["dec_009", "dec_010"])
        },
    )
    assert result.answer.count("dec_009") == 1
    assert "dec_010" in result.answer
    assert "superseded or reversed by dec_009, dec_010" in result.answer


def test_mixed_active_and_historical_does_not_refuse() -> None:
    active = candidate("dec_001")
    old = candidate("dec_002", status=DecisionState.SUPERSEDED)
    result = RefusalHandler().decide(
        understanding=understanding(),
        candidates=[active, old],
        contexts={
            "dec_001": context(active),
            "dec_002": context(old, later_decisions=["dec_009"]),
        },
    )
    assert result is None


def test_contested_status_is_detected() -> None:
    item = candidate("dec_001", status=DecisionState.CONTESTED)
    assert RefusalHandler().is_contested(
        candidates=[item], contexts={"dec_001": context(item)}
    )


def test_conflict_edge_is_detected() -> None:
    item = candidate("dec_001")
    assert RefusalHandler().is_contested(
        candidates=[item],
        contexts={"dec_001": context(item, conflicts=["dec_007"])},
    )


def test_active_decision_is_not_contested() -> None:
    item = candidate("dec_001")
    assert not RefusalHandler().is_contested(
        candidates=[item], contexts={"dec_001": context(item)}
    )


def _contested_pack():
    first = candidate("dec_001", claim="Retries are disabled by default.")
    second = candidate("dec_002", claim="Retries are required for this client.")
    contexts = {
        "dec_001": context(
            first,
            evidence_items=[
                evidence("ev_1", decision_id="dec_001"),
            ],
        ),
        "dec_002": context(
            second,
            evidence_items=[
                evidence("ev_2", decision_id="dec_002", excerpt="Retries are required."),
            ],
        ),
    }
    pack = EvidencePacker().pack(
        understanding=understanding(),
        ranked=[ranked(first), ranked(second)],
        contexts=contexts,
    )
    return pack


def test_contested_response_shows_both_sides_with_evidence() -> None:
    pack = _contested_pack()
    result = RefusalHandler().contested(pack)
    assert result.answer_status is AnswerStatus.CONTESTED
    assert result.mode is RefusalMode.CONTESTED
    assert CONTESTED_MESSAGE in result.answer
    assert "Retries are disabled by default." in result.answer
    assert "Retries are required for this client." in result.answer
    assert "[E1]" in result.answer
    assert "[E2]" in result.answer
    assert result.citations == ["ev_1", "ev_2"]
    assert result.confidence is AnswerConfidence.LOW
    assert result.decision_ids == ["dec_001", "dec_002"]
    assert result.caveats == [
        "The selected decisions conflict; both readings are shown with evidence."
    ]


def test_insufficient_result_carries_nearest_candidates_and_caveats() -> None:
    result = RefusalHandler().insufficient(
        candidates=[candidate("dec_001"), candidate("dec_002")],
        caveats=["validation failed"],
    )
    assert result.answer == VALIDATION_MESSAGE
    assert result.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.confidence is AnswerConfidence.NONE
    assert [item.decision_id for item in result.nearest_candidates] == [
        "dec_001",
        "dec_002",
    ]
    assert result.caveats == ["validation failed"]
