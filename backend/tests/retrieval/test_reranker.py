"""Reranking rules are explicit and explainable — every boost and penalty
has a reason string, and supersession is never treated as current truth."""

from __future__ import annotations

from datetime import date

import pytest
from retrieval_fixtures import (
    FILE_ID,
    candidate,
    context,
    entity,
    evidence,
    understanding,
)

from app.retrieval.config import RetrievalConfig
from app.retrieval.models import (
    DecisionState,
    FusedCandidate,
    RerankSignals,
)
from app.retrieval.retrieval.reranker import Reranker


def _fused(decision_id: str = "dec_001", score: float = 0.01) -> FusedCandidate:
    return FusedCandidate(candidate=candidate(decision_id), score=score)


def test_direct_graph_path_and_target_match_are_detected() -> None:
    item = context(candidate("dec_001"))
    signals = Reranker().signals_for(
        candidate("dec_001"), item, understanding()
    )
    assert signals.graph_path_to_exact_entity is True
    assert signals.target_matches_entity is True
    assert signals.target_related is True


def test_no_context_means_no_graph_path_signal() -> None:
    signals = Reranker().signals_for(candidate("dec_001"), None, understanding())
    assert signals.graph_path_to_exact_entity is False


def test_independent_evidence_sources_are_counted_by_kind() -> None:
    item = context(
        candidate("dec_001"),
        evidence_items=[
            evidence("ev_1", kind="review_comment"),
            evidence("ev_2", kind="commit"),
            evidence("ev_3", kind="commit"),
        ],
    )
    signals = Reranker().signals_for(candidate("dec_001"), item, understanding())
    assert signals.independent_evidence_sources == 2


def test_review_comment_anchor_requires_a_region() -> None:
    anchored = context(candidate("dec_001"))
    assert Reranker().signals_for(
        candidate("dec_001"), anchored, understanding()
    ).review_comment_on_region is True

    floating = context(
        candidate("dec_001"),
        evidence_items=[
            evidence("ev_1", kind="review_comment", path=None, start_line=None)
        ],
    )
    assert Reranker().signals_for(
        candidate("dec_001"), floating, understanding()
    ).review_comment_on_region is False


def test_later_decision_marks_a_candidate_as_superseded() -> None:
    item = context(candidate("dec_001"), later_decisions=["dec_009"])
    signals = Reranker().signals_for(candidate("dec_001"), item, understanding())
    assert signals.superseded is True


def test_unrelated_target_is_penalized() -> None:
    item = context(candidate("dec_001"))
    wrong_entity = understanding(entities=[entity("config", "timeout")])
    signals = Reranker().signals_for(candidate("dec_001"), item, wrong_entity)
    assert signals.target_matches_entity is False
    assert signals.target_related is False


def test_single_evidence_item_is_weak() -> None:
    item = context(
        candidate("dec_001"),
        evidence_items=[evidence("ev_1")],
    )
    signals = Reranker().signals_for(candidate("dec_001"), item, understanding())
    assert signals.single_weak_evidence is True
    assert signals.independent_evidence_sources == 1


def test_stale_evidence_without_a_supersession_check_is_flagged() -> None:
    item = context(candidate("dec_001"))
    no_check = understanding(requires_supersession_check=False)
    signals = Reranker(today=date(2026, 1, 1)).signals_for(
        candidate("dec_001"), item, no_check
    )
    assert signals.stale_without_supersession_check is True


def test_fresh_evidence_is_not_stale_even_without_a_check() -> None:
    item = context(
        candidate("dec_001"),
        evidence_items=[
            evidence("ev_1", created_at="2025-12-31T23:00:00Z"),
            evidence("ev_2", kind="commit", created_at="2025-12-30T23:00:00Z"),
        ],
    )
    no_check = understanding(requires_supersession_check=False)
    signals = Reranker(today=date(2026, 1, 1)).signals_for(
        candidate("dec_001"), item, no_check
    )
    assert signals.stale_without_supersession_check is False


def test_stale_window_is_configurable() -> None:
    item = context(
        candidate("dec_001"),
        evidence_items=[
            evidence("ev_1", created_at="2025-12-01T00:00:00Z"),
            evidence("ev_2", kind="commit", created_at="2025-12-01T00:00:00Z"),
        ],
    )
    no_check = understanding(requires_supersession_check=False)
    narrow = Reranker(
        RetrievalConfig(stale_evidence_days=20), today=date(2026, 1, 1)
    )
    wide = Reranker(
        RetrievalConfig(stale_evidence_days=365), today=date(2026, 1, 1)
    )
    assert narrow.signals_for(
        candidate("dec_001"), item, no_check
    ).stale_without_supersession_check is True
    assert wide.signals_for(
        candidate("dec_001"), item, no_check
    ).stale_without_supersession_check is False


def test_evidence_without_a_date_is_not_called_old() -> None:
    item = context(
        candidate("dec_001"),
        evidence_items=[evidence("ev_1", created_at=None)],
    )
    no_check = understanding(requires_supersession_check=False)
    signals = Reranker(today=date(2026, 1, 1)).signals_for(
        candidate("dec_001"), item, no_check
    )
    assert signals.stale_without_supersession_check is False


def test_later_decision_suppresses_the_stale_penalty() -> None:
    item = context(candidate("dec_001"), later_decisions=["dec_009"])
    no_check = understanding(requires_supersession_check=False)
    signals = Reranker(today=date(2026, 1, 1)).signals_for(
        candidate("dec_001"), item, no_check
    )
    assert signals.stale_without_supersession_check is False


def test_graph_path_requires_an_exact_entity_identity() -> None:
    item = context(candidate("dec_001"))
    # ``retry`` is a substring of ``retry.max_attempts`` but not the entity
    # identity, so it may be relevant without earning the exact-path bonus.
    loose = understanding(entities=[entity("config", "retry")])
    signals = Reranker().signals_for(candidate("dec_001"), item, loose)
    assert signals.target_matches_entity is True
    assert signals.target_related is True
    assert signals.graph_path_to_exact_entity is False


def test_lexical_extraction_of_an_exact_identifier_still_earns_the_path() -> None:
    item = context(candidate("dec_001"))
    lexical = understanding(
        entities=[entity("config", "retry.max_attempts", match="lexical")]
    )
    signals = Reranker().signals_for(candidate("dec_001"), item, lexical)
    assert signals.graph_path_to_exact_entity is True


def test_file_entity_matches_a_region_target_exactly() -> None:
    item = context(candidate("dec_001"))
    file_question = understanding(entities=[entity("file", FILE_ID)])
    signals = Reranker().signals_for(candidate("dec_001"), item, file_question)
    assert signals.graph_path_to_exact_entity is True
    assert signals.target_matches_entity is True


def test_review_comment_outside_the_governed_region_is_not_an_anchor() -> None:
    item = context(
        candidate("dec_001"),
        evidence_items=[
            evidence("ev_1", start_line=500, end_line=505),
            evidence("ev_2", kind="commit", path=None, start_line=None),
        ],
    )
    signals = Reranker().signals_for(candidate("dec_001"), item, understanding())
    assert signals.review_comment_on_region is False


def test_review_comment_in_another_file_is_not_an_anchor() -> None:
    item = context(
        candidate("dec_001"),
        evidence_items=[
            evidence("ev_1", path="httpx/_models.py", start_line=312),
            evidence("ev_2", kind="commit", path=None, start_line=None),
        ],
    )
    signals = Reranker().signals_for(candidate("dec_001"), item, understanding())
    assert signals.review_comment_on_region is False


def test_review_comment_on_a_file_target_is_an_anchor() -> None:
    item = context(
        candidate("dec_001"),
        targets=[f"File:{FILE_ID}"],
        evidence_items=[evidence("ev_1", path=FILE_ID, start_line=400)],
    )
    signals = Reranker().signals_for(candidate("dec_001"), item, understanding())
    assert signals.review_comment_on_region is True


def test_several_items_of_one_kind_are_still_one_weak_source() -> None:
    item = context(
        candidate("dec_001"),
        evidence_items=[
            evidence("ev_1", kind="commit", path=None, start_line=None),
            evidence("ev_2", kind="commit", path=None, start_line=None),
        ],
    )
    signals = Reranker().signals_for(candidate("dec_001"), item, understanding())
    assert signals.independent_evidence_sources == 1
    assert signals.single_weak_evidence is True


def test_two_kinds_are_not_a_single_weak_source() -> None:
    signals = Reranker().signals_for(
        candidate("dec_001"), context(candidate("dec_001")), understanding()
    )
    assert signals.independent_evidence_sources == 2
    assert signals.single_weak_evidence is False


def test_all_documented_bonuses_are_additive() -> None:
    signals = RerankSignals(
        graph_path_to_exact_entity=True,
        independent_evidence_sources=2,
        superseded=False,
        review_comment_on_region=True,
        target_matches_entity=True,
        target_related=True,
        single_weak_evidence=False,
    )
    ranked = Reranker().apply_signals(_fused(score=0.01), signals)
    config = RetrievalConfig()
    expected = (
        0.01
        + config.graph_path_bonus
        + config.multi_evidence_bonus
        + config.active_bonus
        + config.review_anchor_bonus
        + config.target_match_bonus
    )
    assert ranked.final_score == pytest.approx(expected)
    assert len(ranked.reasons) == 5


def test_supersession_replaces_the_active_bonus_with_a_penalty() -> None:
    signals = RerankSignals(superseded=True)
    ranked = Reranker().apply_signals(_fused(score=0.05), signals)
    assert ranked.final_score == pytest.approx(
        0.05 - RetrievalConfig().superseded_penalty
    )
    assert any("historical" in reason for reason in ranked.reasons)


def test_unrelated_target_penalty_is_applied() -> None:
    signals = RerankSignals(target_related=False)
    ranked = Reranker().apply_signals(_fused(score=0.05), signals)
    assert ranked.final_score == pytest.approx(
        0.05
        + RetrievalConfig().active_bonus
        - RetrievalConfig().unrelated_target_penalty
    )


def test_single_weak_evidence_penalty_is_applied() -> None:
    signals = RerankSignals(
        single_weak_evidence=True, independent_evidence_sources=1
    )
    ranked = Reranker().apply_signals(_fused(score=0.05), signals)
    assert ranked.final_score == pytest.approx(
        0.05
        + RetrievalConfig().active_bonus
        - RetrievalConfig().single_weak_evidence_penalty
    )


def test_stale_check_penalty_is_applied() -> None:
    signals = RerankSignals(stale_without_supersession_check=True)
    ranked = Reranker().apply_signals(_fused(score=0.05), signals)
    assert ranked.final_score == pytest.approx(
        0.05
        + RetrievalConfig().active_bonus
        - RetrievalConfig().stale_without_check_penalty
    )


def test_rerank_caps_the_final_set_at_five() -> None:
    fused = [
        FusedCandidate(candidate=candidate(f"dec_{number}"), score=1.0 / number)
        for number in range(1, 9)
    ]
    ranked = Reranker().rerank(
        fused=fused,
        contexts={},
        understanding=understanding(),
    )
    assert len(ranked) == 5


def test_rerank_is_sorted_by_final_score_then_id() -> None:
    first = FusedCandidate(candidate=candidate("dec_b"), score=0.01)
    second = FusedCandidate(candidate=candidate("dec_a"), score=0.01)
    ranked = Reranker().rerank(
        fused=[first, second],
        contexts={},
        understanding=understanding(entities=[]),
    )
    assert [item.candidate.decision_id for item in ranked] == ["dec_a", "dec_b"]


def test_rerank_can_promote_a_lower_fused_exact_match() -> None:
    matching = candidate(
        "dec_match", target_ids=["ConfigKey:retry.max_attempts"]
    )
    unrelated = candidate("dec_other", target_ids=["ConfigKey:timeout"])
    fused = [
        FusedCandidate(candidate=unrelated, score=0.10),
        FusedCandidate(candidate=matching, score=0.08),
    ]
    contexts = {
        "dec_match": context(matching, targets=[]),
        "dec_other": context(
            unrelated,
            targets=[],
            evidence_items=[
                evidence("ev_other", kind="commit", decision_id="dec_other")
            ],
        ),
    }
    ranked = Reranker().rerank(
        fused=fused, contexts=contexts, understanding=understanding()
    )
    assert [item.candidate.decision_id for item in ranked] == [
        "dec_match",
        "dec_other",
    ]
    assert ranked[0].signals.graph_path_to_exact_entity is True
    assert ranked[1].signals.target_related is False


def test_later_decision_is_written_back_to_the_candidate_status() -> None:
    item = context(candidate("dec_001"), later_decisions=["dec_009"])
    fused = FusedCandidate(candidate=candidate("dec_001"), score=0.5)
    ranked = Reranker().rerank(
        fused=[fused],
        contexts={"dec_001": item},
        understanding=understanding(),
    )
    assert ranked[0].candidate.status is DecisionState.SUPERSEDED
    assert ranked[0].candidate.superseded_by == "dec_009"


def test_rerank_is_stable() -> None:
    fused = [FusedCandidate(candidate=candidate("dec_001"), score=0.2)]
    contexts = {"dec_001": context(candidate("dec_001"))}
    reranker = Reranker()
    assert reranker.rerank(
        fused=fused, contexts=contexts, understanding=understanding()
    ) == reranker.rerank(
        fused=fused, contexts=contexts, understanding=understanding()
    )
