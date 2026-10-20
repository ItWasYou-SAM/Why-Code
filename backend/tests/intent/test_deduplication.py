"""Phase 7 duplicate candidate, merge and conflict contracts."""

from __future__ import annotations

from app.intent.graph.cypher_templates import INTENT_REL_TYPES
from app.intent.llm.schemas import DuplicateVerdict
from app.intent.models import (
    Confidence,
    DecisionIntent,
    DecisionStatus,
    DecisionType,
    ResolvedTarget,
    TargetType,
    TemporalScope,
)
from app.intent.validation.deduplicator import (
    ConflictEdge,
    Deduplicator,
    DuplicateCandidate,
    normalize_claim,
)

REPO = "encode/httpx"
CONFIG = "httpx:config:httpx/_config.py:retries"
FILE = "httpx:file:httpx/_config.py@abc"
SYMBOL = "httpx:symbol:httpx/_client.py@abc:Client.send"


def target(
    target_type: TargetType = TargetType.CONFIG_KEY,
    key: str = CONFIG,
) -> ResolvedTarget:
    return ResolvedTarget(type=target_type, identifier=key, graph_key=key)


def decision(
    intent_id: str,
    pr_number: int,
    claim: str,
    *,
    evidence_ids: list[str] | None = None,
    rationale: str = "The same operational rationale.",
    targets: list[ResolvedTarget] | None = None,
    valid_from: str | None = None,
    decision_type: DecisionType = DecisionType.DESIGN_DECISION,
    status: DecisionStatus = DecisionStatus.ACTIVE,
) -> DecisionIntent:
    return DecisionIntent(
        id=intent_id,
        repo=REPO,
        pr_number=pr_number,
        claim=claim,
        rationale=rationale,
        targets=targets if targets is not None else [target()],
        evidence_ids=evidence_ids or [f"httpx:commit:{pr_number}"],
        confidence=Confidence.MEDIUM,
        decision_type=decision_type,
        temporal_scope=(
            TemporalScope(valid_from=valid_from) if valid_from else None
        ),
        status=status,
    )



class FixedEmbedder:
    """Two-dimensional stand-in for a semantic embedding model."""

    def embed(self, text: str) -> list[float]:
        if "retry" in text.casefold():
            return [1.0, 0.0]
        return [0.0, 1.0]


def confidence(relationship: str, candidate: DuplicateCandidate) -> DuplicateVerdict:
    return DuplicateVerdict(
        earlier_intent_id=candidate.earlier.id,
        later_intent_id=candidate.later.id,
        relationship=relationship,
        evidence_ids=[candidate.earlier.evidence_ids[0]],
        explanation="test verdict",
    )


class TestNormalization:
    def test_lowercases_strips_punctuation_and_collapses_whitespace(self) -> None:
        assert normalize_claim("  Retries,   RETRY?!  only. ") == (
            "retries retry only"
        )


class TestCandidateGeneration:
    def test_exact_normalized_claims_are_candidates(self) -> None:
        earlier = decision(
            "dec_a", 100, "Retries are limited to connection errors."
        )
        later = decision(
            "dec_b", 200, "  RETRIES are limited to connection errors! "
        )
        candidates = Deduplicator().candidates([earlier, later])
        assert len(candidates) == 1
        assert candidates[0].exact is True
        assert candidates[0].target_keys == (CONFIG,)

    def test_embedding_similarity_is_only_a_candidate_generator(self) -> None:
        earlier = decision("dec_a", 100, "Retry policy one.")
        later = decision("dec_b", 200, "Retry policy two.")
        deduplicator = Deduplicator(threshold=0.8, embedder=FixedEmbedder())
        candidates = deduplicator.candidates([earlier, later])
        assert len(candidates) == 1
        assert candidates[0].exact is False
        assert candidates[0].similarity == 1.0
        # Similarity alone does not merge; the adjudicator still decides.
        result = deduplicator.adjudicate(
            [earlier, later],
            classifier=lambda candidate: confidence("keep_separate", candidate),
        )
        assert len(result.intents) == 2
        assert not result.merges
        assert result.decisions[0].applied == "kept"

    def test_target_overlap_is_required(self) -> None:
        earlier = decision(
            "dec_a",
            100,
            "The same claim.",
            targets=[target(TargetType.FILE, FILE)],
        )
        later = decision(
            "dec_b",
            200,
            "The same claim.",
            targets=[target(TargetType.FILE, FILE)],
        )
        assert Deduplicator().candidates([earlier, later]) == []

    def test_same_pr_and_terminal_status_are_not_candidates(self) -> None:
        first = decision("dec_a", 100, "The same claim.")
        second = decision("dec_b", 100, "The same claim.")
        assert Deduplicator().candidates([first, second]) == []
        third = decision(
            "dec_c",
            200,
            "The same claim.",
            status=DecisionStatus.SUPERSEDED,
        )
        assert Deduplicator().candidates([first, third]) == []

    def test_temporal_scopes_are_candidates_but_cannot_merge(self) -> None:
        earlier = decision(
            "dec_a",
            100,
            "The same claim.",
            valid_from="2024-01-01T00:00:00Z",
        )
        earlier = earlier.model_copy(
            update={
                "temporal_scope": TemporalScope(
                    valid_from="2024-01-01T00:00:00Z",
                    valid_until="2024-03-01T00:00:00Z",
                )
            }
        )
        later = decision(
            "dec_b",
            200,
            "The same claim.",
            valid_from="2024-06-01T00:00:00Z",
        )
        candidates = Deduplicator().candidates([earlier, later])
        assert len(candidates) == 1
        assert candidates[0].temporal_compatible is False
        result = Deduplicator().adjudicate([earlier, later])
        assert len(result.intents) == 2
        assert not result.merges
        assert result.decisions[0].relationship == "keep_separate"


class TestMergeRules:
    def test_exact_duplicate_merges_evidence_and_removes_the_later_node(self) -> None:
        earlier = decision(
            "dec_a",
            100,
            "Retries require explicit configuration.",
            evidence_ids=["httpx:commit:100"],
        )
        later = decision(
            "dec_b",
            200,
            "Retries require explicit configuration.",
            evidence_ids=["httpx:commit:200"],
        )
        result = Deduplicator().adjudicate([earlier, later])
        assert [intent.id for intent in result.intents] == ["dec_a"]
        merged = result.intents[0]
        assert merged.merged_from == ["dec_b"]
        assert merged.evidence_ids == ["httpx:commit:100", "httpx:commit:200"]
        assert len(result.merges) == 1
        assert result.merges[0].later_id == "dec_b"

    def test_high_impact_merge_waits_for_human_review(self) -> None:
        earlier = decision(
            "dec_a",
            100,
            "Authentication must use the public contract.",
            decision_type=DecisionType.API_CONTRACT,
        )
        later = decision(
            "dec_b",
            200,
            "Authentication must use the public contract.",
            decision_type=DecisionType.API_CONTRACT,
        )
        result = Deduplicator().adjudicate(
            [earlier, later],
            classifier=lambda candidate: confidence("merge", candidate),
        )
        assert len(result.intents) == 2
        assert not result.merges
        assert result.decisions[0].high_impact is True
        assert result.decisions[0].applied == "review"
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_b"].duplicate_of == "dec_a"

    def test_conflict_keeps_both_and_writes_a_conflicts_edge(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision("dec_b", 200, "Retries are disabled by default.")
        result = Deduplicator(threshold=0.5).adjudicate(
            [earlier, later],
            classifier=lambda candidate: confidence("conflicts", candidate),
        )
        assert len(result.intents) == 2
        assert not result.merges
        assert result.conflict_edges == [
            ConflictEdge(
                start_id="dec_b",
                end_id="dec_a",
                reason="test verdict",
            )
        ]
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.CONTESTED
        assert by_id["dec_b"].status is DecisionStatus.CONTESTED
        assert by_id["dec_a"].contested_by == "dec_b"
        assert by_id["dec_b"].contested_by == "dec_a"

    def test_insufficient_verdict_does_not_merge(self) -> None:
        earlier = decision("dec_a", 100, "The same claim.")
        later = decision("dec_b", 200, "The same claim.")
        result = Deduplicator().adjudicate(
            [earlier, later],
            classifier=lambda candidate: confidence(
                "insufficient_evidence", candidate
            ),
        )
        assert len(result.intents) == 2
        assert not result.merges
        assert result.decisions[0].applied == "review"

    def test_conflicts_with_is_an_allowed_intent_relationship(self) -> None:
        assert "CONFLICTS_WITH" in INTENT_REL_TYPES
