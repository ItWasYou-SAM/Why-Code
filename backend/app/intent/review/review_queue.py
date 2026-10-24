"""Human review queue.

Rejected, ambiguous, duplicate or contested outputs are never dropped and
never auto-merged. They are written to a JSONL queue with the reason and the
raw model text (truncated) so a reviewer can decide what, if anything, to do.

Phase 9 turns the queue into a decision surface: every item carries the
candidate identity, the claim under review, a ``needs_review`` status, the
cited evidence, the resolved targets, the proposed confidence and the
reviewer's own decision. Reviewers act through one of the phase-9 actions —
``approve``, ``reject``, ``edit_claim``, ``change_status``, ``mark_contested``
or ``mark_duplicate`` — and the decision is written back with the item.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.core.errors import InvalidRecordError
from app.intent.ids import review_item_id
from app.intent.models import Confidence, ReviewItem, ReviewReason

RAW_LIMIT = 1500

STATUS_NEEDS_REVIEW = "needs_review"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EDITED = "edited"
STATUS_CONTESTED = "contested"
STATUS_DUPLICATE = "duplicate"

REVIEW_ACTIONS = (
    "approve",
    "reject",
    "edit_claim",
    "change_status",
    "mark_contested",
    "mark_duplicate",
)


class ReviewQueue:
    def __init__(
        self,
        *,
        repo: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = repo
        self._clock = clock or (lambda: datetime.now(UTC))
        self.items: list[ReviewItem] = []

    def add(
        self,
        reason: ReviewReason,
        *,
        pr_number: int | None,
        candidate_class: str | None = None,
        claim: str = "",
        issues: list[str] | None = None,
        raw_output: str | None = None,
        evidence: list[str] | None = None,
        targets: list[str] | None = None,
        proposed_confidence: Confidence | str | None = None,
    ) -> ReviewItem:
        claim = claim.strip()
        confidence = Confidence(proposed_confidence) if proposed_confidence else Confidence.MEDIUM
        item = ReviewItem(
            candidate_id=review_item_id(
                repo=self.repo,
                reason=reason.value,
                pr_number=pr_number,
                claim=claim,
            ),
            reason=reason,
            repo=self.repo,
            pr_number=pr_number,
            candidate_class=candidate_class,
            claim=claim[:600],
            status=STATUS_NEEDS_REVIEW,
            evidence=list(evidence or []),
            targets=list(targets or []),
            proposed_confidence=confidence,
            issues=list(issues or []),
            raw_output=(
                raw_output[:RAW_LIMIT] if isinstance(raw_output, str) else None
            ),
            created_at=self._clock().isoformat(),
        )
        self.items.append(item)
        return item

    def apply(
        self,
        candidate_id: str,
        action: str,
        *,
        claim: str | None = None,
        status: str | None = None,
    ) -> ReviewItem:
        """Apply one human review action to a queued candidate.

        Actions are the phase-9 review vocabulary: ``approve``, ``reject``,
        ``edit_claim``, ``change_status``, ``mark_contested`` and
        ``mark_duplicate``. An unknown action or unknown candidate is refused
        instead of silently recorded.
        """
        item = next(
            (entry for entry in self.items if entry.candidate_id == candidate_id),
            None,
        )
        if item is None:
            raise InvalidRecordError(
                f"no review item {candidate_id}",
                component="intent",
                record_id=candidate_id,
            )
        if action not in REVIEW_ACTIONS:
            raise InvalidRecordError(
                f"unknown review action {action}",
                component="intent",
                record_id=candidate_id,
            )
        item.reviewer_decision = action
        if action == "approve":
            item.status = STATUS_APPROVED
        elif action == "reject":
            item.status = STATUS_REJECTED
        elif action == "edit_claim":
            item.claim = (claim or item.claim).strip()[:600]
            item.status = STATUS_EDITED
        elif action == "change_status":
            item.status = status or item.status
        elif action == "mark_contested":
            item.status = STATUS_CONTESTED
        elif action == "mark_duplicate":
            item.status = STATUS_DUPLICATE
        return item

    @classmethod
    def from_jsonl(cls, path: str | Path, *, repo: str) -> list[ReviewItem]:
        """Load a written review queue back into items (reviewer edits)."""
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        items: list[ReviewItem] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            payload = json.loads(stripped)
            items.append(
                ReviewItem.model_validate(
                    {
                        **payload,
                        "repo": payload.get("repo") or repo,
                    }
                )
            )
        return items

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.reason.value] = counts.get(item.reason.value, 0) + 1
        return counts

    def as_dicts(self) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in self.items]

    def write_jsonl(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            json.dumps(item.model_dump(mode="json"), sort_keys=True)
            for item in self.items
        ]
        out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return out


__all__ = ["REVIEW_ACTIONS", "ReviewQueue"]
