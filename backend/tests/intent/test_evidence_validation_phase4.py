"""Phase 4 evidence-validation contracts.

The public result is the small JSON object the plan specifies. The tests also
pin the local decisions behind it: no evidence is discarded, an unknown
citation or target rejects the candidate, a partially unresolved target is
downgraded to an observation, unsupported prose goes to human review,
unsupported participants are dropped, and conflicting evidence is marked
contested rather than silently resolved.
"""

from __future__ import annotations

from intent_helpers import REPO, FakeStore, records, structural_records

from app.domain.records import EvidenceRecord
from app.intent.llm.schemas import ExtractedCandidate
from app.intent.models import Confidence, DecisionStatus
from app.intent.selection.evidence_selector import EvidenceSelector
from app.intent.validation.evidence_validator import (
    EvidenceValidator,
    FinalStatus,
)
from app.intent.validation.graph_link_validator import GraphIndex, GraphLinkValidator

RETRY_COMMENT = "httpx:review_comment:123"
RETRY_REVIEW = "httpx:review:2001"
RETRY_COMMIT = "httpx:commit:" + "a" * 40
RETRY_DIFF = "httpx:pr:1419:file:httpx/_client.py"
PR_MERGED_AT = "2024-04-02T06:30:45Z"


def candidate(**overrides):
    payload = {
        "claim": "Retries are limited to connection errors.",
        "decision_type": "design_decision",
        "status": "accepted",
        "targets": [{"target_type": "config_key", "target_id": "retries"}],
        "evidence_ids": [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF],
        "participants": [],
        "rationale": "Retrying a read or write can duplicate server-side effects.",
        "temporal_scope": {"valid_from": PR_MERGED_AT, "valid_until": None},
    }
    payload.update(overrides)
    return payload


def fixture():
    return records("retry_pr_evidence.json")


def index(rows=None):
    return GraphIndex.from_sources(
        repo=REPO,
        normalized_records=rows or fixture(),
        structural_records=structural_records(),
    )


def bundle(rows=None):
    return EvidenceSelector(REPO).select(rows or fixture())[0]


def validate(
    payload: dict,
    *,
    rows=None,
    require_graph: bool = True,
    allow_target_downgrade: bool = True,
    reject_unsupported_participants: bool = False,
):
    rows = rows or fixture()
    current = bundle(rows)
    graph = index(rows)
    model = ExtractedCandidate.model_validate(payload)
    targets = GraphLinkValidator(graph).validate_targets(
        model.targets,
        prefer_paths=current.changed_paths(),
        prefer_commit=current.latest_commit_sha(),
    )
    validator = EvidenceValidator(
        FakeStore(rows), require_graph=require_graph
    )
    return validator.validate_candidate(
        model,
        bundle=current,
        allowed_ids=set(current.evidence_ids()),
        graph_evidence_ids=set(graph.node_ids) if require_graph else None,
        target_validation=targets,
        decision_id="dec_001",
        allow_target_downgrade=allow_target_downgrade,
        reject_unsupported_participants=reject_unsupported_participants,
    )


class TestResultShape:
    def test_result_is_the_planned_json_shape(self) -> None:
        validation = validate(candidate())
        assert set(validation.result.model_dump()) == {
            "decision_id",
            "schema_valid",
            "evidence_valid",
            "targets_valid",
            "unsupported_claims",
            "invalid_evidence_ids",
            "invalid_target_ids",
            "final_status",
        }
        assert validation.result.decision_id == "dec_001"
        assert validation.result.final_status is FinalStatus.ACCEPTED_FOR_GRAPH
        assert validation.result.schema_valid
        assert validation.result.evidence_valid
        assert validation.result.targets_valid

    def test_no_evidence_fails_schema_before_grounding(self) -> None:
        rows = fixture()
        current = bundle(rows)
        graph = index(rows)
        validator = EvidenceValidator(FakeStore(rows))
        validation = validator.validate_candidate(
            candidate(evidence_ids=[]),
            bundle=current,
            allowed_ids=set(current.evidence_ids()),
            graph_evidence_ids=set(graph.node_ids),
            target_validation=GraphLinkValidator(graph).validate_targets(
                [], prefer_paths=current.changed_paths()
            ),
            decision_id="dec_no_evidence",
        )
        assert not validation.result.schema_valid
        assert validation.result.final_status is FinalStatus.DISCARDED
        assert not validation.accepted


class TestEvidenceGate:
    def test_unknown_citation_is_rejected(self) -> None:
        bad = candidate(
            evidence_ids=["httpx:commit:" + "f" * 40],
        )
        validation = validate(bad)
        assert validation.result.final_status is FinalStatus.REJECTED
        assert validation.result.invalid_evidence_ids == [
            "httpx:commit:" + "f" * 40
        ]

    def test_citation_outside_the_pack_is_rejected(self) -> None:
        rows = fixture()
        current = bundle(rows)
        graph = index(rows)
        validator = EvidenceValidator(FakeStore(rows))
        model = ExtractedCandidate.model_validate(candidate())
        targets = GraphLinkValidator(graph).validate_targets(
            model.targets, prefer_paths=current.changed_paths()
        )
        validation = validator.validate_candidate(
            model,
            bundle=current,
            allowed_ids={RETRY_COMMIT},
            graph_evidence_ids=set(graph.node_ids),
            target_validation=targets,
        )
        assert validation.result.final_status is FinalStatus.REJECTED
        assert RETRY_COMMENT in validation.result.invalid_evidence_ids


class TestTargetGate:
    def test_no_structural_target_is_rejected(self) -> None:
        validation = validate(candidate(targets=[]))
        assert validation.result.final_status is FinalStatus.REJECTED
        assert not validation.result.targets_valid
        assert "no_structural_target" in validation.reason_codes

    def test_unknown_target_is_rejected(self) -> None:
        validation = validate(
            candidate(targets=[{"target_type": "symbol", "target_id": "Missing"}])
        )
        assert validation.result.final_status is FinalStatus.REJECTED
        assert validation.result.invalid_target_ids == ["symbol:Missing"]

    def test_partial_targets_become_an_observation(self) -> None:
        validation = validate(
            candidate(
                targets=[
                    {"target_type": "config_key", "target_id": "retries"},
                    {"target_type": "symbol", "target_id": "Missing"},
                ]
            )
        )
        assert validation.result.final_status is FinalStatus.OBSERVATION
        assert validation.canonical_status is DecisionStatus.UNCERTAIN
        assert validation.extraction_status is not None
        assert validation.result.invalid_target_ids == ["symbol:Missing"]
        # Phase 5: an observation is canonically uncertain, so its score is
        # discounted (0.58 × 0.6 ≈ 0.35) even though the evidence is intact.
        assert validation.confidence.level is Confidence.LOW
        assert validation.confidence.score == 0.35

    def test_partial_targets_reject_when_downgrade_is_disabled(self) -> None:
        validation = validate(
            candidate(
                targets=[
                    {"target_type": "config_key", "target_id": "retries"},
                    {"target_type": "symbol", "target_id": "Missing"},
                ]
            ),
            allow_target_downgrade=False,
        )
        assert validation.result.final_status is FinalStatus.REJECTED


class TestClaimGate:
    def test_claim_not_supported_by_the_evidence_needs_review(self) -> None:
        claim = "The team adopted GraphQL for all internal services."
        validation = validate(candidate(claim=claim))
        assert validation.result.final_status is FinalStatus.NEEDS_REVIEW
        assert claim in validation.result.unsupported_claims

    def test_invented_temporal_date_needs_review(self) -> None:
        validation = validate(
            candidate(
                temporal_scope={
                    "valid_from": "2024-05-05T00:00:00Z",
                    "valid_until": None,
                }
            )
        )
        assert validation.result.final_status is FinalStatus.NEEDS_REVIEW
        assert any(
            "temporal_date_not_in_evidence" in issue
            for issue in validation.result.unsupported_claims
        )
        assert "temporal_invalid" in validation.reason_codes

    def test_reversal_without_a_commit_is_sent_to_review(self) -> None:
        validation = validate(
            candidate(
                claim="Revert the default retry behaviour.",
                decision_type="reversal",
                evidence_ids=[RETRY_COMMENT, RETRY_REVIEW],
            )
        )
        assert validation.result.final_status is FinalStatus.NEEDS_REVIEW
        assert "reversal_without_later_change" in validation.reason_codes


class TestParticipantGate:
    def test_unknown_participant_is_dropped(self) -> None:
        validation = validate(
            candidate(
                participants=[
                    {
                        "login": "mallory",
                        "role": "reviewer",
                        "evidence_ids": [RETRY_COMMENT],
                    }
                ]
            )
        )
        assert validation.result.final_status is FinalStatus.ACCEPTED_FOR_GRAPH
        assert validation.participant_validation.rejected == [
            "mallory:reviewer"
        ]
        assert not validation.participant_validation.accepted

    def test_known_reviewer_is_kept(self) -> None:
        validation = validate(
            candidate(
                participants=[
                    {
                        "login": "lovelydinosaur",
                        "role": "reviewer",
                        "evidence_ids": [RETRY_COMMENT],
                    }
                ]
            )
        )
        assert validation.participant_validation.accepted

    def test_participant_rejection_can_reject_the_candidate(self) -> None:
        validation = validate(
            candidate(
                participants=[
                    {
                        "login": "mallory",
                        "role": "reviewer",
                        "evidence_ids": [RETRY_COMMENT],
                    }
                ]
            ),
            reject_unsupported_participants=True,
        )
        assert validation.result.final_status is FinalStatus.NEEDS_REVIEW


class TestConfidenceGate:
    def test_one_weak_source_is_low_confidence(self) -> None:
        validation = validate(candidate(evidence_ids=[RETRY_COMMENT]))
        assert validation.result.final_status is FinalStatus.ACCEPTED_FOR_GRAPH
        assert validation.confidence.level is Confidence.LOW

    def test_review_comment_commit_and_diff_are_medium_confidence(self) -> None:
        validation = validate(candidate())
        assert validation.confidence.level is Confidence.MEDIUM

    def test_merged_pr_comment_commit_and_diff_are_high_confidence(self) -> None:
        validation = validate(
            candidate(
                evidence_ids=[
                    "httpx:pr:1419",
                    RETRY_COMMENT,
                    RETRY_COMMIT,
                    RETRY_DIFF,
                ]
            )
        )
        assert validation.confidence.level is Confidence.HIGH


class TestConflictGate:
    def test_conflicting_evidence_marks_the_decision_contested(self) -> None:
        rows = [
            *fixture(),
            EvidenceRecord.model_validate(
                {
                    "record_id": "httpx:review_comment:999",
                    "record_type": "review_comment",
                    "repo": REPO,
                    "pr_number": 1419,
                    "payload": {
                        "body": "I disagree; we should not retry connection errors.",
                        "author": "skeptic",
                        "path": "httpx/_client.py",
                        "line": 110,
                        "created_at": "2024-03-30T09:15:00Z",
                    },
                    "content_hash": "synthetic",
                    "ingested_at": "2024-03-30T09:15:00Z",
                }
            ),
        ]
        validation = validate(
            candidate(
                evidence_ids=[
                    RETRY_COMMENT,
                    RETRY_COMMIT,
                    RETRY_DIFF,
                    "httpx:review_comment:999",
                ]
            ),
            rows=rows,
            require_graph=False,
        )
        assert validation.result.final_status is FinalStatus.ACCEPTED_FOR_GRAPH
        assert validation.canonical_status is DecisionStatus.CONTESTED
        assert validation.grounding is not None
        assert validation.grounding.conflicting_evidence_ids == [
            "httpx:review_comment:999"
        ]
