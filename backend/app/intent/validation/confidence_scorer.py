"""Deterministic, categorical confidence (Phase 5).

The model never supplies a confidence value. The score is a reproducible
function of the evidence that survived validation:

* evidence kinds contribute fixed points (a merged PR is stronger than a
  commit, a diff is stronger than discussion alone);
* each additional independent author adds a small corroboration bonus;
* an uncertain candidate is discounted;
* the total is capped below 1.0 because lexical evidence cannot prove intent
  with certainty.

The numeric score is an audit value, stored beside the level so a reader can
reproduce it. User interfaces render only the categorical label — never a
percentage such as "87.3% confident", which would imply statistical certainty
this system does not have.

An empty evidence set is ``insufficient`` rather than ``low``: "no usable
evidence" is a different statement from "weak evidence".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.intent.models import Confidence, ConfidenceResult
from app.intent.selection.evidence_selector import EvidenceItem

KIND_WEIGHTS: dict[str, float] = {
    "merged_pr": 0.20,
    "commit_message": 0.15,
    "diff": 0.20,
    "review_comment": 0.20,
    "issue_comment": 0.10,
    "doc": 0.15,
}

AUTHOR_BONUS = 0.03
MAX_AUTHOR_BONUS = 3 * AUTHOR_BONUS
UNCERTAIN_FACTOR = 0.6
MAX_SCORE = 0.98
HIGH_THRESHOLD = 0.75
MEDIUM_THRESHOLD = 0.45

# Display vocabulary. The backend stores the level; the UI must render only
# these labels, never the numeric score.
DISPLAY_LABELS: dict[Confidence, str] = {
    Confidence.HIGH: "High confidence",
    Confidence.MEDIUM: "Medium confidence",
    Confidence.LOW: "Low confidence",
    Confidence.INSUFFICIENT: "Insufficient evidence",
}

# Retained for callers that describe the evidence families.
HUMAN_DISCUSSION = {"review", "review_comment", "issue_comment"}
CHANGE = {"commit", "changed_file", "doc_file"}

_KIND_BY_RECORD_TYPE: dict[str, str] = {
    "pull_request": "merged_pr",
    "commit": "commit_message",
    "changed_file": "diff",
    "doc_file": "doc",
    "review": "review_comment",
    "review_comment": "review_comment",
    "issue": "issue_comment",
    "issue_comment": "issue_comment",
}


@dataclass(frozen=True)
class _EvidenceFact:
    record_type: str
    kind: str | None
    author: str | None


class ConfidenceScorer:
    def score(
        self,
        evidence: Iterable[EvidenceItem | str | dict[str, Any]],
        *,
        status: str | None = None,
    ) -> ConfidenceResult:
        facts = [_fact(item) for item in evidence]
        source_types = sorted(
            {fact.record_type for fact in facts if fact.record_type}
        )
        kinds = sorted({fact.kind for fact in facts if fact.kind})

        if not kinds:
            return ConfidenceResult(
                level=Confidence.INSUFFICIENT,
                score=0.0,
                reasons=[
                    "no usable evidence supports the claim",
                    f"types: {', '.join(source_types) or 'none'}",
                ],
                source_types=source_types,
            )

        reasons = [
            f"{kind.replace('_', ' ')} evidence (+{KIND_WEIGHTS[kind]:.2f})"
            for kind in kinds
        ]
        score = sum(KIND_WEIGHTS[kind] for kind in kinds)

        authors = sorted({fact.author for fact in facts if fact.author})
        independent = max(len(authors) - 1, 0)
        bonus = min(independent * AUTHOR_BONUS, MAX_AUTHOR_BONUS)
        if bonus:
            score += bonus
            reasons.append(
                f"{len(authors)} independent authors (+{bonus:.2f})"
            )

        if _status_value(status) == "uncertain":
            score *= UNCERTAIN_FACTOR
            reasons.append("status is uncertain (score × 0.60)")

        score = round(min(score, MAX_SCORE), 2)
        if score >= HIGH_THRESHOLD:
            level = Confidence.HIGH
        elif score >= MEDIUM_THRESHOLD:
            level = Confidence.MEDIUM
        else:
            level = Confidence.LOW
        reasons.append(
            f"score {score:.2f} selects {DISPLAY_LABELS[level].lower()}"
        )

        return ConfidenceResult(
            level=level,
            score=score,
            reasons=reasons,
            source_types=source_types,
        )


def display_label(level: Confidence) -> str:
    """The only wording a UI should ever show for a confidence level."""
    return DISPLAY_LABELS[level]


def _fact(item: EvidenceItem | str | dict[str, Any]) -> _EvidenceFact:
    if isinstance(item, str):
        return _EvidenceFact(item, _kind(item, None), None)
    if isinstance(item, dict):
        payload = item.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        record_type = str(
            item.get("record_type") or item.get("source_type") or ""
        )
        merged = item.get("merged")
        if merged is None:
            merged = payload.get("merged")
        author = item.get("author")
        if not author:
            author = payload.get("author") or payload.get("login")
        return _EvidenceFact(
            record_type,
            _kind(record_type, merged),
            str(author) if author else None,
        )
    return _EvidenceFact(
        item.record_type,
        _kind(item.record_type, item.payload.get("merged")),
        item.author,
    )


def _kind(record_type: str, merged: object) -> str | None:
    kind = _KIND_BY_RECORD_TYPE.get(record_type)
    if kind == "merged_pr" and merged is False:
        return None
    return kind


def _status_value(status: object) -> str:
    if status is None:
        return ""
    value = getattr(status, "value", status)
    return str(value)


__all__ = [
    "AUTHOR_BONUS",
    "CHANGE",
    "ConfidenceScorer",
    "DISPLAY_LABELS",
    "HIGH_THRESHOLD",
    "HUMAN_DISCUSSION",
    "KIND_WEIGHTS",
    "MAX_SCORE",
    "MEDIUM_THRESHOLD",
    "UNCERTAIN_FACTOR",
    "display_label",
]
