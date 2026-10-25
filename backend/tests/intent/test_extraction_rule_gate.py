"""Phase 3 hard-rule contracts.

These are the rules that cannot be encoded in JSON Schema: refactor honesty,
weak-comment classification, PR rejection, reversal grounding, participant
evidence and temporal sanity. Rejections go to the review queue; status
downgrades are recorded on the candidate.
"""

from __future__ import annotations

from dataclasses import replace

from intent_helpers import REPO, records

from app.intent.llm.schemas import ExtractedCandidate
from app.intent.selection.evidence_selector import EvidenceSelector
from app.intent.validation.extraction_rules import ExtractionRuleValidator
from app.intent.validation.graph_link_validator import TargetValidation

RETRY_COMMENT = "httpx:review_comment:123"
RETRY_COMMIT = "httpx:commit:" + "a" * 40
RETRY_DIFF = "httpx:pr:1419:file:httpx/_client.py"


def bundle():
    return EvidenceSelector(REPO).select(records("retry_pr_evidence.json"))[0]


def candidate(**overrides) -> ExtractedCandidate:
    payload = {
        "claim": "Retries are limited to connection errors.",
        "decision_type": "design_decision",
        "status": "accepted",
        "targets": [],
        "evidence_ids": [RETRY_COMMENT, RETRY_COMMIT, RETRY_DIFF],
        "participants": [],
        "rationale": "Only connect failures are safe to repeat.",
        "temporal_scope": {"valid_from": "2024-04-02", "valid_until": None},
    }
    payload.update(overrides)
    return ExtractedCandidate.model_validate(payload)


def validate(model: ExtractedCandidate, *, evidence_ids=None, target_validation=None):
    current = bundle()
    items = [
        current.items_by_id().get(evidence_id)
        for evidence_id in (evidence_ids or model.evidence_ids)
    ]
    return ExtractionRuleValidator().validate(
        model,
        bundle=current,
        evidence_items=[item for item in items if item is not None],
        target_validation=target_validation or TargetValidation(),
    )


class TestRefactorRule:
    def test_refactor_without_rationale_is_rejected(self) -> None:
        result = validate(
            candidate(
                claim="Refactor the client request path.",
                rationale="",
            )
        )
        assert not result.ok
        assert [issue.code for issue in result.issues] == [
            "refactor_without_rationale"
        ]

    def test_refactor_with_rationale_is_allowed(self) -> None:
        result = validate(
            candidate(
                claim="Refactor the client request path.",
                rationale="The old path duplicated redirect handling.",
            )
        )
        assert result.ok


class TestWeakEvidenceRule:
    def test_review_comment_alone_downgrades_to_uncertain(self) -> None:
        result = validate(
            candidate(evidence_ids=[RETRY_COMMENT]),
            evidence_ids=[RETRY_COMMENT],
        )
        assert result.ok
        assert result.status == "uncertain"
        assert result.adjusted_status == "uncertain"

    def test_corroborated_comment_keeps_accepted(self) -> None:
        result = validate(candidate())
        assert result.status == "accepted"
        assert result.adjusted_status is None


class TestPrStateRule:
    def test_unmerged_pr_cannot_be_accepted(self) -> None:
        current = bundle()
        unmerged = replace(
            current,
            pr=replace(
                current.pr,
                payload={**current.pr.payload, "merged": False},
            ),
        )
        model = candidate()
        result = ExtractionRuleValidator().validate(
            model,
            bundle=unmerged,
            evidence_items=[current.items_by_id().get(RETRY_COMMENT)],
            target_validation=TargetValidation(),
        )
        assert result.ok
        assert result.status == "rejected"

    def test_merged_pr_with_rationale_can_be_accepted(self) -> None:
        result = validate(candidate())
        assert result.status == "accepted"


class TestReversalRule:
    def test_reversal_without_revert_language_is_rejected(self) -> None:
        result = validate(
            candidate(
                claim="Change the retry default.",
                decision_type="reversal",
                rationale="The new default is cleaner.",
            )
        )
        assert not result.ok
        assert [issue.code for issue in result.issues] == [
            "reversal_without_evidence"
        ]

    def test_accepted_reversal_is_classified_as_reversed(self) -> None:
        result = validate(
            candidate(
                claim="Revert the default retry behaviour.",
                decision_type="reversal",
                status="accepted",
                rationale="The previous default duplicated writes.",
            )
        )
        assert result.status == "reversed"


class TestParticipantAndTemporalRules:
    def test_participant_evidence_must_be_cited(self) -> None:
        result = validate(
            candidate(
                participants=[
                    {
                        "login": "reviewer",
                        "role": "reviewer",
                        "evidence_ids": ["httpx:review_comment:999"],
                    }
                ]
            )
        )
        assert not result.ok
        assert [issue.code for issue in result.issues] == [
            "participant_evidence_not_cited"
        ]

    def test_temporal_scope_must_be_ordered(self) -> None:
        result = validate(
            candidate(
                temporal_scope={
                    "valid_from": "2024-06-01",
                    "valid_until": "2024-01-01",
                }
            )
        )
        assert not result.ok
        assert [issue.code for issue in result.issues] == [
            "invalid_temporal_scope"
        ]


class TestTargetRule:
    def test_unresolved_target_rejects_the_candidate(self) -> None:
        result = validate(
            candidate(),
            target_validation=TargetValidation(
                issues=["Symbol 'Missing' is not present in the loaded graph"],
                dropped=["symbol:Missing"],
            ),
        )
        assert not result.ok
        assert [issue.code for issue in result.issues] == [
            "target_outside_changed_set"
        ]
