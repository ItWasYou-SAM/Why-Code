"""Human review queue.

Rejected, ambiguous, duplicate or contested outputs are never dropped and
never auto-merged. They are written to a JSONL queue with the reason and the
raw model text (truncated) so a reviewer can decide what, if anything, to do.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.intent.ids import review_item_id
from app.intent.models import ReviewItem, ReviewReason

RAW_LIMIT = 1500


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
    ) -> ReviewItem:
        claim = claim.strip()
        item = ReviewItem(
            item_id=review_item_id(
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
            issues=list(issues or []),
            raw_output=(
                raw_output[:RAW_LIMIT] if isinstance(raw_output, str) else None
            ),
            created_at=self._clock().isoformat(),
        )
        self.items.append(item)
        return item

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


__all__ = ["ReviewQueue"]
