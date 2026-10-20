"""Phase 6 supersession contracts.

Candidates are found deterministically from a shared specific target and a
later date. The adjudicator chooses ``reverses``, ``revises``, ``supersedes``,
``unrelated`` or ``insufficient_evidence``; only the first three create an
edge, and a conflicting later claim still marks the earlier decision
``contested`` without inventing a relationship.
"""

from __future__ import annotations

from intent_helpers import REPO

from app.intent.graph.cypher_templates import INTENT_REL_TYPES
from app.intent.llm.schemas import SupersessionVerdict
from app.intent.models import (
    Confidence,
    DecisionIntent,
    DecisionStatus,
    ResolvedTarget,
    ReversalIntent,
    TargetType,
)
from app.intent.selection.evidence_selector import EvidenceItem
from app.intent.validation.supersession import (
    SIGNAL_CODE_REGION,
    SIGNAL_CONFIG_REMOVAL,
    SIGNAL_DOC_CHANGE,
    SIGNAL_RETAINED_RATIONALE,
    SIGNAL_REVERSAL_LANGUAGE,
    SIGNAL_REVIEW_FAILED,
    SupersessionCandidate,
    SupersessionDetector,
)

DATES = {100: "2024-01-01T00:00:00Z", 200: "2024-06-01T00:00:00Z"}
CONFIG = "httpx:config:httpx/_config.py:retries"
COMMIT = "httpx:commit:abc"


def target(type_: TargetType = TargetType.CONFIG_KEY, key: str = CONFIG) -> ResolvedTarget:
    return ResolvedTarget(type=type_, identifier=key, graph_key=key)


def decision(
    id_: str,
    pr: int,
    claim: str,
    *,
    status: DecisionStatus = DecisionStatus.ACTIVE,
    evidence: str = COMMIT,
    targets: list[ResolvedTarget] | None = None,
    rationale: str = "",
) -> DecisionIntent:
    return DecisionIntent(
        id=id_,
        repo=REPO,
        pr_number=pr,
        claim=claim,
        rationale=rationale,
        targets=targets if targets is not None else [target()],
        evidence_ids=[evidence],
        confidence=Confidence.MEDIUM,
        status=status,
    )


def reversal(id_: str, pr: int, claim: str, reversed_claim: str) -> ReversalIntent:
    return ReversalIntent(
        id=id_,
        repo=REPO,
        pr_number=pr,
        claim=claim,
        reversed_claim=reversed_claim,
        targets=[target()],
        evidence_ids=["httpx:commit:def"],
        confidence=Confidence.MEDIUM,
        reversal_type="revert",
    )


def verdict(
    candidate: SupersessionCandidate,
    relationship: str,
    *,
    evidence_ids: list[str] | None = None,
    explanation: str = "test verdict",
) -> SupersessionVerdict:
    return SupersessionVerdict(
        earlier_decision_id=candidate.earlier.id,
        later_decision_id=candidate.later.id,
        relationship=relationship,  # type: ignore[arg-type]
        evidence_ids=evidence_ids or [candidate.earlier.evidence_ids[0]],
        explanation=explanation,
    )


def classifier(relationship: str):
    return lambda candidate: verdict(candidate, relationship)


class TestRelationshipEdges:
    def test_reverses_creates_a_later_to_earlier_edge(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision("dec_b", 200, "Retries are disabled by default.")
        result = SupersessionDetector().detect(
            [earlier, later],
            pr_dates=DATES,
            classifier=classifier("reverses"),
        )
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.REVERSED
        assert by_id["dec_a"].superseded_by == "dec_b"
        edge = next(edge for edge in result.edges if edge.rel_type == "REVERSES")
        assert edge.start_id == "dec_b"
        assert edge.end_id == "dec_a"

    def test_revises_marks_the_earlier_decision_superseded(self) -> None:
        earlier = decision("dec_a", 100, "Retries use a fixed delay.")
        later = decision("dec_b", 200, "Retries use exponential backoff.")
        result = SupersessionDetector().detect(
            [earlier, later],
            pr_dates=DATES,
            classifier=classifier("revises"),
        )
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.SUPERSEDED
        assert by_id["dec_a"].superseded_by == "dec_b"
        assert any(edge.rel_type == "REVISES" for edge in result.edges)

    def test_supersedes_creates_a_supersedes_edge(self) -> None:
        earlier = decision("dec_a", 100, "Retries use a fixed delay.")
        later = decision("dec_b", 200, "The retry helper is replaced entirely.")
        result = SupersessionDetector().detect(
            [earlier, later],
            pr_dates=DATES,
            classifier=classifier("supersedes"),
        )
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.SUPERSEDED
        assert by_id["dec_a"].superseded_by == "dec_b"
        edge = next(edge for edge in result.edges if edge.rel_type == "SUPERSEDES")
        assert edge.start_id == "dec_b"


class TestNoEdgeVerdicts:
    def test_unrelated_creates_no_edge(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision("dec_a_later", 200, "Retries are enabled by default.")
        result = SupersessionDetector().detect(
            [earlier, later],
            pr_dates=DATES,
            classifier=classifier("unrelated"),
        )
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.ACTIVE
        assert result.edges == []

    def test_insufficient_evidence_creates_no_edge_and_no_contest(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision("dec_b", 200, "Requests must never be retried at all.")
        result = SupersessionDetector().detect(
            [earlier, later],
            pr_dates=DATES,
            classifier=classifier("insufficient_evidence"),
        )
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.ACTIVE
        assert result.edges == []
        assert result.decisions[0].relationship == "insufficient_evidence"

    def test_conflicting_unrelated_claim_is_contested_without_an_edge(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision("dec_b", 200, "Requests must never be retried at all.")
        result = SupersessionDetector().detect(
            [earlier, later],
            pr_dates=DATES,
            classifier=classifier("unrelated"),
        )
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.CONTESTED
        assert by_id["dec_a"].contested_by == "dec_b"
        assert result.edges == []


class TestVerdictValidation:
    def test_verdict_for_the_wrong_pair_is_insufficient(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision("dec_b", 200, "Retries are disabled by default.")

        def wrong_pair(candidate: SupersessionCandidate) -> SupersessionVerdict:
            return SupersessionVerdict(
                earlier_decision_id="dec_other",
                later_decision_id=candidate.later.id,
                relationship="reverses",
                evidence_ids=[candidate.earlier.evidence_ids[0]],
                explanation="wrong pair",
            )

        result = SupersessionDetector().detect(
            [earlier, later], pr_dates=DATES, classifier=wrong_pair
        )
        assert result.edges == []
        assert result.decisions[0].relationship == "insufficient_evidence"

    def test_verdict_citing_outside_evidence_is_insufficient(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision("dec_b", 200, "Retries are disabled by default.")
        result = SupersessionDetector().detect(
            [earlier, later],
            pr_dates=DATES,
            classifier=lambda candidate: verdict(
                candidate,
                "reverses",
                evidence_ids=["httpx:commit:invented"],
            ),
        )
        assert result.edges == []
        assert result.decisions[0].relationship == "insufficient_evidence"


def evidence_item(record_id: str, record_type: str, text: str) -> EvidenceItem:
    return EvidenceItem(
        record_id=record_id,
        record_type=record_type,
        pr_number=200,
        payload={"body": text},
    )


class TestCandidateQuery:
    def test_a_duplicate_review_pair_is_not_a_supersession_candidate(self) -> None:
        earlier = decision("dec_a", 100, "The same claim.")
        later = decision("dec_b", 200, "The same claim.").model_copy(
            update={"duplicate_of": "dec_a"}
        )
        candidates = SupersessionDetector().find_candidates(
            [earlier, later], pr_dates=DATES
        )
        assert candidates == []

    def test_candidate_records_every_detection_signal(self) -> None:
        rationale = "Retrying a read or write can duplicate server-side effects."
        shared_targets = [
            target(),
            target(TargetType.CODE_REGION, "httpx:region:httpx/_config.py:1-10@abc"),
        ]
        earlier = decision(
            "dec_a",
            100,
            "Retries are enabled by default.",
            targets=shared_targets,
            rationale=rationale,
        )
        later = decision(
            "dec_b",
            200,
            "Retries change for the shared code region.",
            targets=shared_targets,
            rationale=rationale,
        )
        evidence = {
            "httpx:commit:new": evidence_item(
                "httpx:commit:new", "commit", "Remove the retries config key."
            ),
            "httpx:doc:1": evidence_item(
                "httpx:doc:1", "doc_file", "Retries are no longer supported."
            ),
            "httpx:review:1": evidence_item(
                "httpx:review:1", "review_comment", "This approach did not work."
            ),
        }
        candidates = SupersessionDetector().find_candidates(
            [earlier, later],
            pr_dates=DATES,
            evidence_by_id=evidence,
            evidence_by_pr={200: list(evidence.values())},
        )
        assert len(candidates) == 1
        assert set(candidates[0].signals) == {
            SIGNAL_REVERSAL_LANGUAGE,
            SIGNAL_CONFIG_REMOVAL,
            SIGNAL_CODE_REGION,
            SIGNAL_DOC_CHANGE,
            SIGNAL_REVIEW_FAILED,
            SIGNAL_RETAINED_RATIONALE,
        }

    def test_changed_config_key_is_detected_from_the_diff(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision("dec_b", 200, "The retry default changes.")
        changed = EvidenceItem(
            record_id="httpx:pr:200:file:httpx/_config.py",
            record_type="changed_file",
            pr_number=200,
            payload={
                "path": "httpx/_config.py",
                "patch": "@@ -1,4 +1,4 @@\n-    retries = 5\n+    retries = 3",
            },
        )
        candidates = SupersessionDetector().find_candidates(
            [earlier, later],
            pr_dates=DATES,
            evidence_by_id={changed.record_id: changed},
            evidence_by_pr={200: [changed]},
        )
        assert SIGNAL_CONFIG_REMOVAL in candidates[0].signals
        assert SIGNAL_REVERSAL_LANGUAGE not in candidates[0].signals

    def test_pr_title_is_scanned_even_when_the_body_is_empty(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision(
            "dec_b",
            200,
            "The retry behaviour changes.",
            evidence="httpx:pull_request:200",
        )
        pr = EvidenceItem(
            record_id="httpx:pull_request:200",
            record_type="pull_request",
            pr_number=200,
            payload={"title": "Revert the default retry behaviour", "body": ""},
        )
        candidates = SupersessionDetector().find_candidates(
            [earlier, later],
            pr_dates=DATES,
            evidence_by_id={pr.record_id: pr},
            evidence_by_pr={200: [pr]},
        )
        assert SIGNAL_REVERSAL_LANGUAGE in candidates[0].signals

    def test_only_three_supersession_relationship_types_are_allowed(self) -> None:
        assert {"REVERSES", "REVISES", "SUPERSEDES"} <= set(
            INTENT_REL_TYPES
        )
        assert "SUPERSEDED_BY" not in INTENT_REL_TYPES

    def test_later_reversal_node_is_processed_before_a_decision(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later_decision = decision(
            "dec_c", 300, "Retries are disabled by default."
        )
        reversal_node = reversal(
            "rev_a",
            300,
            "Revert default retries.",
            "Retries are enabled by default.",
        )

        def choose(candidate: SupersessionCandidate) -> SupersessionVerdict:
            relationship = (
                "reverses"
                if isinstance(candidate.later, ReversalIntent)
                else "supersedes"
            )
            return verdict(candidate, relationship)

        result = SupersessionDetector().detect(
            [earlier, later_decision, reversal_node],
            pr_dates={**DATES, 300: "2024-09-01T00:00:00Z"},
            classifier=choose,
        )
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.REVERSED
        assert isinstance(by_id["rev_a"], ReversalIntent)
        assert by_id["rev_a"].reverses == ["dec_a"]
        assert [edge.rel_type for edge in result.edges] == ["REVERSES"]
        assert result.edges[0].start_id == "rev_a"

    def test_rejected_later_decision_does_not_supersede(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        rejected = decision(
            "dec_b",
            200,
            "Retries are disabled by default.",
            status=DecisionStatus.REJECTED,
        )
        result = SupersessionDetector().detect(
            [earlier, rejected],
            pr_dates=DATES,
            classifier=classifier("reverses"),
        )
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.ACTIVE
        assert result.edges == []

    def test_same_pr_decisions_are_not_superseded(self) -> None:
        first = decision("dec_a", 100, "Retries are enabled by default.")
        second = decision("dec_b", 100, "Retries are disabled by default.")
        result = SupersessionDetector().detect(
            [first, second],
            pr_dates=DATES,
            classifier=classifier("reverses"),
        )
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.ACTIVE

    def test_existing_decision_yields_a_status_update_not_a_new_node(self) -> None:
        existing = decision("dec_old", 100, "Retries are enabled by default.")
        later = decision("dec_new", 200, "Retries are disabled by default.")
        result = SupersessionDetector().detect(
            [later],
            pr_dates=DATES,
            existing_decisions=[existing],
            classifier=classifier("reverses"),
        )
        assert [intent.id for intent in result.intents] == ["dec_new"]
        assert len(result.status_updates) == 1
        update = result.status_updates[0]
        assert update.intent_id == "dec_old"
        assert update.status is DecisionStatus.REVERSED
        assert update.superseded_by == "dec_new"

    def test_file_targets_do_not_supersede_on_their_own(self) -> None:
        file_target = target(TargetType.FILE, "httpx:file:httpx/_client.py@a")
        earlier = decision(
            "dec_a", 100, "The client keeps the redirect loop.", targets=[file_target]
        )
        later = decision(
            "dec_b",
            200,
            "The client removes the redirect loop.",
            targets=[file_target],
        )
        result = SupersessionDetector().detect(
            [earlier, later],
            pr_dates=DATES,
            classifier=classifier("reverses"),
        )
        assert result.candidates == []
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.ACTIVE


class TestDeterministicFallback:
    def test_fallback_reverses_when_language_and_claim_overlap(self) -> None:
        earlier = decision("dec_a", 100, "Retries are enabled by default.")
        later = decision(
            "dec_b", 200, "Retries are disabled by default and no longer enabled."
        )
        result = SupersessionDetector().detect([earlier, later], pr_dates=DATES)
        by_id = {intent.id: intent for intent in result.intents}
        assert by_id["dec_a"].status is DecisionStatus.REVERSED
        assert result.edges[0].rel_type == "REVERSES"

    def test_fallback_revises_when_rationale_is_retained(self) -> None:
        rationale = "Retrying a read or write can duplicate server-side effects."
        earlier = decision(
            "dec_a", 100, "Retries use a fixed delay.", rationale=rationale
        )
        later = decision(
            "dec_b", 200, "Retries use exponential backoff.", rationale=rationale
        )
        result = SupersessionDetector().detect([earlier, later], pr_dates=DATES)
        assert result.edges[0].rel_type == "REVISES"
