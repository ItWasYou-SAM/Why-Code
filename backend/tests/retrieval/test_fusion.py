"""WRRF combines ranks, not incomparable raw scores. These tests pin the
formula, the configured weights, source-rank handling and deterministic
tie-breaking."""

from __future__ import annotations

import pytest
from retrieval_fixtures import candidate

from app.retrieval.config import RetrievalConfig
from app.retrieval.models import DecisionState, RetrievalSource
from app.retrieval.retrieval.fusion import Fusion


def _expected(weight: float, rank: int, k: int = 60) -> float:
    return weight / (k + rank)


def test_exact_source_formula() -> None:
    fusion = Fusion()
    rows = fusion.fuse(
        {RetrievalSource.EXACT: [candidate("dec_001")]}
    )
    assert rows[0].score == pytest.approx(_expected(5.0, 1))
    assert rows[0].contributions == {
        RetrievalSource.EXACT: pytest.approx(5.0 / 61)
    }


def test_all_four_sources_sum_their_contributions() -> None:
    fusion = Fusion()
    rows = fusion.fuse(
        {
            RetrievalSource.EXACT: [candidate("dec_001")],
            RetrievalSource.FULLTEXT: [candidate("dec_002"), candidate("dec_001")],
            RetrievalSource.VECTOR: [
                candidate("dec_003"),
                candidate("dec_004"),
                candidate("dec_001"),
            ],
            RetrievalSource.GRAPH: [candidate("dec_001")],
        }
    )
    by_id = {row.candidate.decision_id: row for row in rows}
    expected = 5.0 / 61 + 2.0 / 62 + 1.5 / 63 + 2.5 / 61
    assert by_id["dec_001"].score == pytest.approx(expected)
    assert len(by_id["dec_001"].contributions) == 4


def test_missing_from_a_source_adds_nothing() -> None:
    rows = Fusion().fuse(
        {
            RetrievalSource.EXACT: [candidate("dec_001")],
            RetrievalSource.FULLTEXT: [candidate("dec_002")],
        }
    )
    by_id = {row.candidate.decision_id: row for row in rows}
    assert by_id["dec_001"].score == pytest.approx(5.0 / 61)
    # Rank is per source: the first full-text candidate is rank 1.
    assert by_id["dec_002"].score == pytest.approx(2.0 / 61)


def test_source_rank_is_used_instead_of_list_position() -> None:
    item = candidate("dec_001", sources={RetrievalSource.FULLTEXT: 9})
    rows = Fusion().fuse({RetrievalSource.FULLTEXT: [item]})
    assert rows[0].score == pytest.approx(2.0 / 69)


def test_configured_k_changes_every_score() -> None:
    rows = Fusion(RetrievalConfig(k=10)).fuse(
        {RetrievalSource.EXACT: [candidate("dec_001")]}
    )
    assert rows[0].score == pytest.approx(5.0 / 11)


def test_weights_change_the_order_without_touching_raw_scores() -> None:
    exact_only = candidate("dec_exact")
    vector_only = candidate("dec_vector")
    results = {
        RetrievalSource.EXACT: [exact_only],
        RetrievalSource.VECTOR: [vector_only, candidate("dec_filler")],
    }
    normal = Fusion().fuse(results)
    weighted = Fusion(
        RetrievalConfig(exact_weight=0.1, vector_weight=50.0)
    ).fuse(results)
    assert normal[0].candidate.decision_id == "dec_exact"
    assert weighted[0].candidate.decision_id == "dec_vector"
    assert {row.candidate.decision_id for row in normal} == {
        row.candidate.decision_id for row in weighted
    }


def test_duplicate_ids_across_sources_are_merged_once() -> None:
    rows = Fusion().fuse(
        {
            RetrievalSource.EXACT: [candidate("dec_001")],
            RetrievalSource.FULLTEXT: [candidate("dec_001")],
        }
    )
    assert len(rows) == 1
    assert rows[0].contributions[RetrievalSource.EXACT] > 0
    assert rows[0].contributions[RetrievalSource.FULLTEXT] > 0


def test_merge_keeps_the_most_severe_status_and_unions_evidence() -> None:
    active = candidate(
        "dec_001", status=DecisionState.ACTIVE, evidence_ids=["ev_1"]
    )
    contested = candidate(
        "dec_001",
        status=DecisionState.CONTESTED,
        claim="The decision is contested.",
        evidence_ids=["ev_2"],
    )
    rows = Fusion().fuse(
        {
            RetrievalSource.EXACT: [active],
            RetrievalSource.FULLTEXT: [contested],
        }
    )
    assert rows[0].candidate.status is DecisionState.CONTESTED
    assert rows[0].candidate.evidence_ids == ["ev_1", "ev_2"]
    assert rows[0].candidate.claim == active.claim


def test_ties_are_broken_by_decision_id() -> None:
    rows = Fusion().fuse(
        {
            RetrievalSource.EXACT: [
                candidate("dec_b"),
                candidate("dec_a"),
            ]
        }
    )
    # Both came from the same source with different ranks, so this is not a
    # tie. A real tie needs separate sources with equal rank and weight.
    assert [row.candidate.decision_id for row in rows] == ["dec_b", "dec_a"]

    tied = Fusion(
        RetrievalConfig(exact_weight=1.0, fulltext_weight=1.0)
    ).fuse(
        {
            RetrievalSource.EXACT: [candidate("dec_b")],
            RetrievalSource.FULLTEXT: [candidate("dec_a")],
        }
    )
    assert [row.candidate.decision_id for row in tied] == ["dec_a", "dec_b"]


def test_empty_input_returns_no_candidates() -> None:
    assert Fusion().fuse({}) == []


def test_scores_are_stable_across_runs() -> None:
    results = {
        RetrievalSource.EXACT: [candidate("dec_001")],
        RetrievalSource.FULLTEXT: [candidate("dec_002")],
    }
    first = Fusion().fuse(results)
    second = Fusion().fuse(results)
    assert first == second
