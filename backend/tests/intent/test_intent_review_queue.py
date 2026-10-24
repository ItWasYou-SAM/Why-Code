"""Phase 9: human review queue contracts.

The queue never drops and never auto-merges: every uncertain extraction lands
here with the phase-9 item shape (candidate, claim, status, reason, evidence,
targets, proposed confidence, reviewer decision) and a reviewer resolves it
through one of the phase-9 actions.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.errors import InvalidRecordError
from app.intent.models import Confidence, ReviewItem, ReviewReason
from app.intent.review.review_queue import (
    REVIEW_ACTIONS,
    ReviewQueue,
)


def _item() -> ReviewItem:
    queue = ReviewQueue(repo="encode/httpx")
    return queue.add(
        ReviewReason.UNSUPPORTED_CLAIM,
        pr_number=1419,
        candidate_class="DECISION_CANDIDATE",
        claim="The team adopted GraphQL for all internal services.",
        issues=["unsupported_claim"],
        raw_output='{"claim": "The team adopted GraphQL for all internal services."}',
        evidence=[
            "httpx:review_comment:123",
            "httpx:commit:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ],
        targets=["httpx:symbol:httpx/_client.py@aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                 ":Client.send"],
        proposed_confidence=Confidence.MEDIUM,
    )


class TestPhaseNineReviewQueue:
    def test_add_creates_a_needs_review_item_with_the_phase9_shape(self) -> None:
        item = _item()
        assert item.candidate_id
        assert item.reason is ReviewReason.UNSUPPORTED_CLAIM
        assert item.status == "needs_review"
        assert item.reviewer_decision is None
        assert item.evidence
        assert item.targets
        assert item.proposed_confidence == Confidence.MEDIUM

    def test_approve_marks_the_item_approved(self) -> None:
        queue = ReviewQueue(repo="encode/httpx")
        item = queue.add(
            ReviewReason.UNSUPPORTED_CLAIM,
            pr_number=1419,
            claim="Retries are limited to connection errors.",
        )
        updated = queue.apply(item.candidate_id, "approve")
        assert updated.status == "approved"
        assert updated.reviewer_decision == "approve"

    def test_reject_marks_the_item_rejected(self) -> None:
        queue = ReviewQueue(repo="encode/httpx")
        item = queue.add(
            ReviewReason.INSUFFICIENT_EVIDENCE,
            pr_number=1419,
            claim="Retries are limited to connection errors.",
        )
        updated = queue.apply(item.candidate_id, "reject")
        assert updated.status == "rejected"
        assert updated.reviewer_decision == "reject"

    def test_edit_claim_replaces_the_claim(self) -> None:
        queue = ReviewQueue(repo="encode/httpx")
        item = queue.add(
            ReviewReason.UNSUPPORTED_CLAIM,
            pr_number=1419,
            claim="Retries are limited to connection errors.",
        )
        updated = queue.apply(
            item.candidate_id, "edit_claim", claim="Retries are limited to retryable errors."
        )
        assert updated.claim == "Retries are limited to retryable errors."
        assert updated.status == "edited"
        assert updated.reviewer_decision == "edit_claim"

    def test_change_status_overrides_the_status(self) -> None:
        queue = ReviewQueue(repo="encode/httpx")
        item = queue.add(
            ReviewReason.CONTESTED,
            pr_number=1419,
            claim="Retries are limited to connection errors.",
        )
        updated = queue.apply(item.candidate_id, "change_status", status="escalated")
        assert updated.status == "escalated"
        assert updated.reviewer_decision == "change_status"

    def test_mark_contested_marks_the_item_contested(self) -> None:
        queue = ReviewQueue(repo="encode/httpx")
        item = queue.add(
            ReviewReason.UNSUPPORTED_CLAIM,
            pr_number=1419,
            claim="Retries are limited to connection errors.",
        )
        updated = queue.apply(item.candidate_id, "mark_contested")
        assert updated.status == "contested"
        assert updated.reviewer_decision == "mark_contested"

    def test_mark_duplicate_marks_the_item_duplicate(self) -> None:
        queue = ReviewQueue(repo="encode/httpx")
        item = queue.add(
            ReviewReason.POSSIBLE_DUPLICATE,
            pr_number=1419,
            claim="Retries are limited to connection errors.",
        )
        updated = queue.apply(item.candidate_id, "mark_duplicate")
        assert updated.status == "duplicate"
        assert updated.reviewer_decision == "mark_duplicate"

    def test_unknown_action_is_refused(self) -> None:
        queue = ReviewQueue(repo="encode/httpx")
        item = queue.add(
            ReviewReason.UNSUPPORTED_CLAIM,
            pr_number=1419,
            claim="Retries are limited to connection errors.",
        )
        with pytest.raises(InvalidRecordError):
            queue.apply(item.candidate_id, "approve_the_intent_now")

    def test_unknown_candidate_is_refused(self) -> None:
        queue = ReviewQueue(repo="encode/httpx")
        with pytest.raises(InvalidRecordError):
            queue.apply("cand_missing", "approve")

    def test_queue_round_trips_through_jsonl(self, tmp_path: Path) -> None:
        queue = ReviewQueue(repo="encode/httpx")
        item = queue.add(
            ReviewReason.INSUFFICIENT_EVIDENCE,
            pr_number=1419,
            claim="Retries are limited to connection errors.",
            evidence=["httpx:review_comment:123"],
        )
        path = queue.write_jsonl(tmp_path / "review_queue.jsonl")
        loaded = ReviewQueue.from_jsonl(path, repo="encode/httpx")
        assert [entry.candidate_id for entry in loaded] == [item.candidate_id]
        assert loaded[0].status == "needs_review"
        assert loaded[0].evidence == ["httpx:review_comment:123"]

    def test_review_actions_are_the_phase9_vocabulary(self) -> None:
        assert set(REVIEW_ACTIONS) == {
            "approve",
            "reject",
            "edit_claim",
            "change_status",
            "mark_contested",
            "mark_duplicate",
        }
