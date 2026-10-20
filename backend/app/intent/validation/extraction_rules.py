"""Deterministic enforcement of the Phase 3 extraction rules.

The provider schema is the first gate, not the last one. This module applies
the rules that cannot be expressed as JSON Schema constraints:

* a candidate must cite at least one evidence ID (schema) that exists in the
  packed context, the store and the graph (``EvidenceValidator``);
* every target must resolve against the structural graph or the PR's changed
  files (``GraphLinkValidator``);
* a refactor without a stated rationale is not a decision;
* a review comment alone is weak evidence and downgrades an otherwise
  accepted candidate to ``uncertain``;
* an unmerged ("rejected") PR cannot produce an ``accepted`` candidate;
* a reversal must cite reversal language;
* participant evidence must be a subset of the candidate's own evidence;
* a model that cannot support a claim answers with ``missing_evidence``
  instead of guessing.

Some rules *reject* a proposal; others *downgrade* its classification. The
canonical lifecycle (active/contested/superseded/reversed) is still computed
later from validated evidence and graph order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.intent.llm.schemas import ExtractedCandidate
from app.intent.selection.evidence_selector import EvidenceItem, PrEvidenceBundle
from app.intent.validation.graph_link_validator import TargetValidation

WEAK_EVIDENCE_TYPES = frozenset({"review_comment"})
REFACTOR_MARKERS = re.compile(
    r"\b(refactor\w*|rename\w*|renam\w*|move[sd]?|mov(?:ing|ed)?|"
    r"clean ?up|restructur\w*|reorganiz\w*|tidy\w*)\b",
    re.IGNORECASE,
)
REVERSAL_MARKERS = re.compile(
    r"\b(revert\w*|remov\w*|deprecat\w*|roll(?:ed|s)? ?back|drop(?:ped|s)?|"
    r"replac\w*|no longer|disable[sd]?|discontinu\w*)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RuleIssue:
    code: str
    message: str


@dataclass
class RuleResult:
    ok: bool
    candidate: ExtractedCandidate
    issues: list[RuleIssue] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    adjusted_status: str | None = None

    @property
    def issue_texts(self) -> list[str]:
        return [f"{issue.code}: {issue.message}" for issue in self.issues]

    @property
    def status(self) -> str:
        return self.adjusted_status or self.candidate.status


class ExtractionRuleValidator:
    """Apply the semantic extraction rules to one validated candidate."""

    def validate(
        self,
        candidate: ExtractedCandidate,
        *,
        bundle: PrEvidenceBundle,
        evidence_items: list[EvidenceItem],
        target_validation: TargetValidation,
    ) -> RuleResult:
        issues: list[RuleIssue] = []
        notes: list[str] = []
        status = candidate.status

        if target_validation.issues:
            issues.append(
                RuleIssue(
                    "target_outside_changed_set",
                    "; ".join(target_validation.issues),
                )
            )

        if _is_refactor(candidate.claim) and not candidate.rationale.strip():
            issues.append(
                RuleIssue(
                    "refactor_without_rationale",
                    "a refactor is not a decision unless the evidence states why",
                )
            )

        evidence_types = {
            item.record_type for item in evidence_items if item.record_type
        }
        if (
            evidence_types
            and evidence_types <= WEAK_EVIDENCE_TYPES
            and status != "uncertain"
        ):
            status = "uncertain"
            notes.append(
                "a review comment alone is weak evidence; status downgraded "
                "to uncertain until corroborated"
            )

        if not _is_merged(bundle) and status != "rejected":
            status = "rejected"
            notes.append(
                "the PR was not merged; the candidate is classified rejected"
            )

        if candidate.decision_type == "reversal":
            if not _has_reversal_language(candidate, evidence_items, bundle):
                issues.append(
                    RuleIssue(
                        "reversal_without_evidence",
                        "a reversal candidate must cite revert/removal language",
                    )
                )
            if status == "accepted":
                status = "reversed"
                notes.append(
                    "a reversal is recorded as reversed; the original decision "
                    "and REVERSES edge are produced deterministically"
                )

        participant_evidence = {
            evidence_id
            for participant in candidate.participants
            for evidence_id in participant.evidence_ids
        }
        outside = participant_evidence - set(candidate.evidence_ids)
        if outside:
            issues.append(
                RuleIssue(
                    "participant_evidence_not_cited",
                    "participant evidence must also be candidate evidence: "
                    + ", ".join(sorted(outside)),
                )
            )

        if (
            candidate.temporal_scope.valid_until is not None
            and candidate.temporal_scope.valid_until
            < candidate.temporal_scope.valid_from
        ):
            issues.append(
                RuleIssue(
                    "invalid_temporal_scope",
                    "valid_until cannot precede valid_from",
                )
            )

        return RuleResult(
            ok=not issues,
            candidate=candidate,
            issues=issues,
            notes=notes,
            adjusted_status=status if status != candidate.status else None,
        )


def _is_refactor(claim: str) -> bool:
    return bool(REFACTOR_MARKERS.search(claim))


def _is_merged(bundle: PrEvidenceBundle) -> bool:
    return bundle.pr.payload.get("merged") is True


def _has_reversal_language(
    candidate: ExtractedCandidate,
    evidence_items: list[EvidenceItem],
    bundle: PrEvidenceBundle,
) -> bool:
    texts = [candidate.claim, candidate.rationale]
    texts.extend(item.text for item in evidence_items)
    # PR metadata is already in the pack, but include the title/body directly
    # so the rule does not depend on whether a section survived the budget.
    texts.extend(
        [
            str(bundle.pr.payload.get("title") or ""),
            str(bundle.pr.payload.get("body") or ""),
        ]
    )
    return any(REVERSAL_MARKERS.search(text) for text in texts if text)


__all__ = [
    "ExtractionRuleValidator",
    "REFACTOR_MARKERS",
    "REVERSAL_MARKERS",
    "RuleIssue",
    "RuleResult",
    "WEAK_EVIDENCE_TYPES",
]
