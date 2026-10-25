"""Graph expansion enriches only candidates that came back from a source,
and produces a deterministic code-to-evidence path."""

from __future__ import annotations

from retrieval_fixtures import REPO, FakeBackend, candidate, context, evidence

from app.retrieval.models import DecisionCandidate
from app.retrieval.retrieval.graph_expander import GraphExpander


def test_expands_candidates_in_retrieval_order() -> None:
    backend = FakeBackend(
        contexts={
            "dec_001": context(candidate("dec_001")),
            "dec_002": context(candidate("dec_002")),
        }
    )
    rows = GraphExpander(backend).expand(
        repo=REPO,
        candidates=[candidate("dec_002"), candidate("dec_001")],
    )
    assert [row.decision.decision_id for row in rows] == ["dec_002", "dec_001"]
    assert [call[1]["decision_id"] for call in backend.calls] == [
        "dec_002",
        "dec_001",
    ]


def test_missing_contexts_are_skipped() -> None:
    backend = FakeBackend(contexts={"dec_001": context(candidate("dec_001"))})
    rows = GraphExpander(backend).expand(
        repo=REPO, candidates=[candidate("nope"), candidate("dec_001")]
    )
    assert [row.decision.decision_id for row in rows] == ["dec_001"]


def test_duplicate_candidates_are_expanded_once() -> None:
    backend = FakeBackend(contexts={"dec_001": context(candidate("dec_001"))})
    GraphExpander(backend).expand(
        repo=REPO, candidates=[candidate("dec_001"), candidate("dec_001")]
    )
    assert len(backend.calls) == 1


def test_context_for_the_wrong_decision_is_rejected() -> None:
    backend = FakeBackend(
        contexts={"dec_001": context(candidate("dec_002"))}
    )
    rows = GraphExpander(backend).expand(
        repo=REPO, candidates=[candidate("dec_001")]
    )
    assert rows == []


def test_expansion_limit_is_enforced() -> None:
    backend = FakeBackend(
        contexts={
            f"dec_{number}": context(candidate(f"dec_{number}"))
            for number in range(10)
        }
    )
    rows = GraphExpander(backend, limit=3).expand(
        repo=REPO,
        candidates=[candidate(f"dec_{number}") for number in range(10)],
    )
    assert len(rows) == 3
    assert len(backend.calls) == 3


def test_empty_candidate_list_does_not_hit_the_backend() -> None:
    backend = FakeBackend()
    assert GraphExpander(backend).expand(repo=REPO, candidates=[]) == []
    assert backend.calls == []


def test_candidate_claim_is_used_when_the_context_has_none() -> None:
    empty = DecisionCandidate(decision_id="dec_001")
    backend = FakeBackend(
        contexts={
            "dec_001": context(
                empty, evidence_items=[evidence("ev_101", decision_id="dec_001")]
            )
        }
    )
    rows = GraphExpander(backend).expand(
        repo=REPO,
        candidates=[
            candidate(
                "dec_001",
                claim="A decision claim from search.",
                evidence_ids=["ev_101"],
            )
        ],
    )
    assert rows[0].decision.claim == "A decision claim from search."


def test_graph_path_is_ordered_from_entity_to_evidence() -> None:
    item = context(candidate("dec_001"))
    path = item.path()
    assert path[0].startswith("ConfigKey:")
    assert path[1].startswith("Symbol:")
    assert path[2].startswith("CodeRegion:")
    assert path[3].startswith("Commit:")
    assert path[4] == "PullRequest:#1234"
    assert path[5] == "Decision:dec_001"
    assert path[6:] == ["Evidence:ev_101", "Evidence:ev_102"]


def test_later_decisions_are_preserved_for_supersession_check() -> None:
    item = context(
        candidate("dec_001"),
        later_decisions=["dec_009"],
        conflicts=["dec_007"],
    )
    assert item.later_decisions == ["dec_009"]
    assert item.conflicts == ["dec_007"]
