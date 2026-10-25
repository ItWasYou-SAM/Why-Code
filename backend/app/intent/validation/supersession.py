"""Supersession, revision and contest detection (Phase 6).

Detection is deliberately two-stage:

1. **Candidate query.** Deterministic code finds pairs of decisions that
   govern the same specific target (symbol, config key or code region), in a
   later PR, and records the signals that made the pair interesting: reversal
   language, a config removal, a code-region edit, a doc change, a review that
   says the approach failed, or a retained rationale.
2. **Adjudication.** The model is asked one narrow question over that pair and
   must return ``reverses``, ``revises``, ``supersedes``, ``unrelated`` or
   ``insufficient_evidence``. Deterministic code owns the edge and lifecycle
   update; ``unrelated`` and ``insufficient_evidence`` never create an edge.

Only three relationship types can be emitted:
``REVERSES``, ``REVISES`` and ``SUPERSEDES``. A later claim that contradicts
the earlier one without reversing it still marks the earlier decision
``contested`` (D-0038), but that is a status, never an edge.

When no classifier is supplied the detector uses a conservative deterministic
fallback. The pipeline always supplies the LLM judge; the fallback exists for
offline replay and unit tests.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from itertools import combinations

from app.intent.edge_utils import dedupe_edges
from app.intent.llm.schemas import (
    SupersessionRelationshipLiteral,
    SupersessionVerdict,
)
from app.intent.models import (
    DecisionIntent,
    DecisionStatus,
    IntentBase,
    ReversalIntent,
    TargetType,
)
from app.intent.selection.evidence_selector import EvidenceItem

REVERSAL_MARKERS = re.compile(
    r"\b(revert\w*|remov\w*|deprecat\w*|roll(?:ed|s)? ?back|drop(?:ped|s)?|"
    r"replac\w*|no longer|disable[sd]?|discontinu\w*)\b",
    re.IGNORECASE,
)
FAILURE_MARKERS = re.compile(
    r"\b(did not work|doesn't work|does not work|failed|failure|not viable|"
    r"broken|regression|unsafe)\b",
    re.IGNORECASE,
)
SPECIFIC_TARGETS = {
    TargetType.SYMBOL,
    TargetType.CONFIG_KEY,
    TargetType.CODE_REGION,
}
TERMINAL_STATUSES = {
    DecisionStatus.SUPERSEDED,
    DecisionStatus.REVERSED,
    DecisionStatus.REJECTED,
}
CONFLICT_SIMILARITY = 0.35
REVISION_SIMILARITY = 0.35

REL_BY_RELATIONSHIP = {
    "reverses": "REVERSES",
    "revises": "REVISES",
    "supersedes": "SUPERSEDES",
}
STATUS_BY_RELATIONSHIP = {
    "reverses": DecisionStatus.REVERSED,
    "revises": DecisionStatus.SUPERSEDED,
    "supersedes": DecisionStatus.SUPERSEDED,
}

SIGNAL_REVERSAL_LANGUAGE = "reversal_language"
SIGNAL_CONFIG_REMOVAL = "config_key_removal"
SIGNAL_CODE_REGION = "same_code_region"
SIGNAL_DOC_CHANGE = "doc_change"
SIGNAL_REVIEW_FAILED = "review_says_failed"
SIGNAL_RETAINED_RATIONALE = "retains_rationale"

_DISCUSSION_TYPES = {"review", "review_comment", "issue_comment"}


@dataclass(frozen=True)
class SupersessionEdge:
    rel_type: str
    start_id: str
    end_id: str
    reason: str


@dataclass(frozen=True)
class StatusUpdate:
    intent_id: str
    status: DecisionStatus
    superseded_by: str | None = None
    contested_by: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class SupersessionCandidate:
    """One later/earlier pair sharing at least one specific target."""

    earlier: IntentBase
    later: IntentBase
    targets: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()
    earlier_date: str = ""
    later_date: str = ""
    evidence_lines: tuple[tuple[str, str], ...] = ()
    context_evidence_ids: tuple[str, ...] = ()

    @property
    def ids(self) -> tuple[str, str]:
        return (self.earlier.id, self.later.id)

    @property
    def allowed_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.earlier.evidence_ids)
                | set(self.later.evidence_ids)
                | set(self.context_evidence_ids)
            )
        )


@dataclass
class _CandidateState:
    earlier: IntentBase
    later: IntentBase
    targets: set[str] = field(default_factory=set)
    signals: set[str] = field(default_factory=set)
    earlier_date: str = ""
    later_date: str = ""


@dataclass
class _StatusChange:
    status: DecisionStatus
    superseded_by: str | None = None
    contested_by: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class SupersessionDecision:
    """The auditable answer for one candidate pair."""

    earlier_decision_id: str
    later_decision_id: str
    relationship: str
    evidence_ids: tuple[str, ...] = ()
    explanation: str = ""
    target_keys: tuple[str, ...] = ()


@dataclass
class SupersessionResult:
    intents: list[IntentBase] = field(default_factory=list)
    edges: list[SupersessionEdge] = field(default_factory=list)
    status_updates: list[StatusUpdate] = field(default_factory=list)
    candidates: list[SupersessionCandidate] = field(default_factory=list)
    decisions: list[SupersessionDecision] = field(default_factory=list)


Classifier = Callable[[SupersessionCandidate], SupersessionVerdict | None]


class SupersessionDetector:
    def find_candidates(
        self,
        intents: list[IntentBase],
        *,
        pr_dates: dict[int, str] | None = None,
        existing_decisions: list[DecisionIntent] | None = None,
        existing_dates: dict[int, str] | None = None,
        evidence_by_id: Mapping[str, EvidenceItem] | None = None,
        evidence_by_pr: Mapping[int, list[EvidenceItem]] | None = None,
    ) -> list[SupersessionCandidate]:
        """Return later/earlier pairs that share a specific target.

        This is the in-memory equivalent of the plan's ``MATCH
        (old:Decision)-[:GOVERNS]->(target)<-[:GOVERNS]-(new:Decision)``
        query, restricted to the loaded graph and the current run.
        """
        pr_dates = pr_dates or {}
        existing = list(existing_decisions or [])
        existing_dates = existing_dates or {}
        evidence_by_id = evidence_by_id or {}
        evidence_by_pr = evidence_by_pr or {}
        existing_ids = {decision.id for decision in existing}
        decisions = [item for item in intents if isinstance(item, DecisionIntent)]
        reversals = [
            item
            for item in intents
            if isinstance(item, ReversalIntent)
            and item.status
            in {DecisionStatus.ACTIVE, DecisionStatus.REVERSED}
        ]
        current_ids = {item.id for item in intents}

        by_target: dict[str, list[IntentBase]] = {}
        for node in [*existing, *decisions, *reversals]:
            for target in node.targets:
                if target.type in SPECIFIC_TARGETS:
                    by_target.setdefault(target.graph_key, []).append(node)

        merged: dict[tuple[str, str], _CandidateState] = {}
        for key in sorted(by_target):
            ordered = sorted(
                by_target[key],
                key=lambda node: (
                    _date_of(node, pr_dates, existing_dates, existing_ids),
                    node.pr_number,
                    node.id,
                ),
            )
            for earlier, later in combinations(ordered, 2):
                if not _candidate_pair(earlier, later, current_ids):
                    continue
                earlier_date = _date_of(
                    earlier, pr_dates, existing_dates, existing_ids
                )
                later_date = _date_of(
                    later, pr_dates, existing_dates, existing_ids
                )
                if earlier_date and later_date and later_date <= earlier_date:
                    continue
                pair = (earlier.id, later.id)
                state = merged.setdefault(
                    pair,
                    _CandidateState(
                        earlier=earlier,
                        later=later,
                        earlier_date=earlier_date,
                        later_date=later_date,
                    ),
                )
                state.targets.add(key)
                state.signals.update(
                    _signals(
                        earlier,
                        later,
                        evidence_by_id,
                        evidence_by_pr=evidence_by_pr,
                    )
                )

        candidates = [
            SupersessionCandidate(
                earlier=state.earlier,
                later=state.later,
                targets=tuple(sorted(state.targets)),
                signals=tuple(sorted(state.signals)),
                earlier_date=state.earlier_date,
                later_date=state.later_date,
                evidence_lines=_evidence_lines(
                    _context_ids(
                        state.earlier,
                        state.later,
                        evidence_by_pr,
                    ),
                    evidence_by_id,
                ),
                context_evidence_ids=_context_ids(
                    state.earlier,
                    state.later,
                    evidence_by_pr,
                ),
            )
            for state in merged.values()
        ]
        # A ReversalIntent is the strongest possible later node. Adjudicate it
        # first so its REVERSES edge is not pre-empted by a later decision that
        # merely restates the same change.
        return sorted(
            candidates,
            key=lambda candidate: (
                not isinstance(candidate.later, ReversalIntent),
                candidate.later_date,
                candidate.earlier.id,
                candidate.later.id,
            ),
        )

    def detect(
        self,
        intents: list[IntentBase],
        *,
        pr_dates: dict[int, str] | None = None,
        existing_decisions: list[DecisionIntent] | None = None,
        existing_dates: dict[int, str] | None = None,
        evidence_by_id: Mapping[str, EvidenceItem] | None = None,
        evidence_by_pr: Mapping[int, list[EvidenceItem]] | None = None,
        classifier: Classifier | None = None,
    ) -> SupersessionResult:
        pr_dates = pr_dates or {}
        existing = list(existing_decisions or [])
        existing_dates = existing_dates or {}
        existing_ids = {decision.id for decision in existing}
        decisions = [item for item in intents if isinstance(item, DecisionIntent)]

        candidates = self.find_candidates(
            intents,
            pr_dates=pr_dates,
            existing_decisions=existing,
            existing_dates=existing_dates,
            evidence_by_id=evidence_by_id,
            evidence_by_pr=evidence_by_pr,
        )
        classify = classifier or heuristic_classifier

        pending: dict[str, _StatusChange] = {}
        edges: list[SupersessionEdge] = []
        decisions_out: list[SupersessionDecision] = []

        def effective(decision: IntentBase) -> DecisionStatus:
            change = pending.get(decision.id)
            return change.status if change else decision.status

        def set_status(
            decision: IntentBase,
            status: DecisionStatus,
            *,
            superseded_by: str | None = None,
            contested_by: str | None = None,
            reason: str = "",
        ) -> None:
            pending[decision.id] = _StatusChange(
                status=status,
                superseded_by=superseded_by,
                contested_by=contested_by,
                reason=reason,
            )

        for candidate in candidates:
            earlier = candidate.earlier
            later = candidate.later
            if effective(earlier) in TERMINAL_STATUSES:
                continue
            if later.status is not DecisionStatus.ACTIVE and not (
                isinstance(later, ReversalIntent)
                and later.status is DecisionStatus.REVERSED
            ):
                continue

            verdict = _validated_verdict(classify(candidate), candidate)
            relationship = verdict.relationship
            if relationship in REL_BY_RELATIONSHIP:
                set_status(
                    earlier,
                    STATUS_BY_RELATIONSHIP[relationship],
                    superseded_by=later.id,
                    reason=(
                        verdict.explanation
                        or f"later decision {later.id} {relationship} this decision"
                    ),
                )
                edges.append(
                    SupersessionEdge(
                        rel_type=REL_BY_RELATIONSHIP[relationship],
                        start_id=later.id,
                        end_id=earlier.id,
                        reason=verdict.explanation or relationship,
                    )
                )
            elif relationship == "unrelated" and _should_contest(candidate):
                if (
                    effective(earlier) is DecisionStatus.ACTIVE
                    and later.status is DecisionStatus.ACTIVE
                ):
                    set_status(
                        earlier,
                        DecisionStatus.CONTESTED,
                        contested_by=later.id,
                        reason=(
                            "a later decision contradicts this target without "
                            "reversing or revising it"
                        ),
                    )

            decisions_out.append(
                SupersessionDecision(
                    earlier_decision_id=earlier.id,
                    later_decision_id=later.id,
                    relationship=relationship,
                    evidence_ids=tuple(verdict.evidence_ids),
                    explanation=verdict.explanation,
                    target_keys=candidate.targets,
                )
            )

        updated_intents = _apply_intent_updates(intents, pending, decisions)
        updated_intents = _apply_reversal_links(updated_intents, edges)
        status_updates = [
            StatusUpdate(
                intent_id=intent_id,
                status=change.status,
                superseded_by=change.superseded_by,
                contested_by=change.contested_by,
                reason=change.reason,
            )
            for intent_id, change in sorted(pending.items())
            if intent_id in existing_ids
        ]
        return SupersessionResult(
            intents=updated_intents,
            edges=dedupe_edges(edges),
            status_updates=status_updates,
            candidates=candidates,
            decisions=decisions_out,
        )


def heuristic_classifier(
    candidate: SupersessionCandidate,
) -> SupersessionVerdict:
    """Conservative fallback used only when no LLM judge is supplied."""
    later = candidate.later
    signals = set(candidate.signals)
    relationship: SupersessionRelationshipLiteral
    if isinstance(later, ReversalIntent):
        relationship = "reverses"
    elif SIGNAL_REVERSAL_LANGUAGE in signals:
        if _similarity(
            candidate.earlier.claim, later.claim
        ) >= CONFLICT_SIMILARITY:
            relationship = "reverses"
        else:
            relationship = "supersedes"
    elif SIGNAL_RETAINED_RATIONALE in signals:
        relationship = "revises"
    else:
        relationship = "unrelated"
    evidence = [*candidate.earlier.evidence_ids, *candidate.later.evidence_ids]
    return SupersessionVerdict(
        earlier_decision_id=candidate.earlier.id,
        later_decision_id=candidate.later.id,
        relationship=relationship,
        evidence_ids=[evidence[0]] if evidence else ["unknown"],
        explanation=(
            "deterministic fallback: "
            + (", ".join(candidate.signals) or "no reversal signal")
        ),
    )


def _validated_verdict(
    verdict: SupersessionVerdict | None,
    candidate: SupersessionCandidate,
) -> SupersessionVerdict:
    """Reject a verdict that names the wrong pair or invents evidence."""
    if verdict is None:
        return _insufficient(candidate, "the judge returned no verdict")
    if verdict.earlier_decision_id != candidate.earlier.id or (
        verdict.later_decision_id != candidate.later.id
    ):
        return _insufficient(
            candidate,
            "the verdict does not name this earlier/later pair",
        )
    allowed = set(candidate.allowed_evidence_ids)
    if allowed and not set(verdict.evidence_ids) <= allowed:
        return _insufficient(
            candidate,
            "the verdict cites evidence outside the two decisions",
        )
    return verdict


def _insufficient(
    candidate: SupersessionCandidate, explanation: str
) -> SupersessionVerdict:
    evidence = [*candidate.earlier.evidence_ids, *candidate.later.evidence_ids]
    return SupersessionVerdict(
        earlier_decision_id=candidate.earlier.id,
        later_decision_id=candidate.later.id,
        relationship="insufficient_evidence",
        evidence_ids=[evidence[0]] if evidence else ["unknown"],
        explanation=explanation,
    )


def _candidate_pair(
    earlier: IntentBase, later: IntentBase, current_ids: set[str]
) -> bool:
    if earlier.id == later.id:
        return False
    if earlier.pr_number == later.pr_number:
        return False
    # A pair already waiting on duplicate review (for example a high-impact
    # merge) must not also become a supersession edge before the human acts.
    if earlier.duplicate_of == later.id or later.duplicate_of == earlier.id:
        return False
    if not isinstance(earlier, DecisionIntent):
        return False
    if earlier.status in TERMINAL_STATUSES:
        return False
    if later.id not in current_ids:
        return False
    if later.status is DecisionStatus.ACTIVE:
        return True
    return (
        isinstance(later, ReversalIntent)
        and later.status is DecisionStatus.REVERSED
    )


def _signals(
    earlier: IntentBase,
    later: IntentBase,
    evidence_by_id: Mapping[str, EvidenceItem],
    *,
    evidence_by_pr: Mapping[int, list[EvidenceItem]],
) -> set[str]:
    signals: set[str] = set()
    texts = [later.claim, later.rationale]
    types: set[str] = set()
    seen: set[str] = set()
    candidates = [
        *(
            item
            for evidence_id in later.evidence_ids
            if (item := evidence_by_id.get(evidence_id)) is not None
        ),
        *evidence_by_pr.get(later.pr_number, []),
    ]
    for item in candidates:
        if item.record_id in seen:
            continue
        seen.add(item.record_id)
        texts.append(_item_text(item))
        types.add(item.record_type)
    text = "\n".join(part for part in texts if part)
    shared_types = _shared_types(earlier, later)
    if REVERSAL_MARKERS.search(text):
        signals.add(SIGNAL_REVERSAL_LANGUAGE)
    if TargetType.CONFIG_KEY in shared_types and (
        SIGNAL_REVERSAL_LANGUAGE in signals
        or _config_key_changed(earlier, later, evidence_by_pr)
    ):
        signals.add(SIGNAL_CONFIG_REMOVAL)
    if TargetType.CODE_REGION in shared_types:
        signals.add(SIGNAL_CODE_REGION)
    if "doc_file" in types:
        signals.add(SIGNAL_DOC_CHANGE)
    if types & _DISCUSSION_TYPES and FAILURE_MARKERS.search(text):
        signals.add(SIGNAL_REVIEW_FAILED)
    if (
        earlier.rationale.strip()
        and later.rationale.strip()
        and _similarity(earlier.rationale, later.rationale)
        >= REVISION_SIMILARITY
    ):
        signals.add(SIGNAL_RETAINED_RATIONALE)
    return signals


def _config_key_changed(
    earlier: IntentBase,
    later: IntentBase,
    evidence_by_pr: Mapping[int, list[EvidenceItem]],
) -> bool:
    """Detect a changed config key in the later PR's diff.

    The target key ends in the config-leaf name (``...:retries``). A changed
    file patch that adds or removes a line containing that leaf is the
    "later commit removes/replaces a config key" signal.
    """
    leaves = {
        target.graph_key.rsplit(":", 1)[-1].lower()
        for target in earlier.targets
        if target.type == TargetType.CONFIG_KEY
    }
    leaves |= {
        target.graph_key.rsplit(":", 1)[-1].lower()
        for target in later.targets
        if target.type == TargetType.CONFIG_KEY
    }
    leaves.discard("")
    if not leaves:
        return False
    for item in evidence_by_pr.get(later.pr_number, []):
        if item.record_type != "changed_file":
            continue
        patch = item.payload.get("patch")
        if not isinstance(patch, str) or not patch:
            continue
        for line in patch.splitlines():
            if not line.startswith(("-", "+")) or line.startswith(("---", "+++")):
                continue
            lowered = line.lower()
            if any(leaf in lowered for leaf in leaves):
                return True
    return False


def _shared_types(
    earlier: IntentBase, later: IntentBase
) -> set[TargetType]:
    earlier_types = {
        target.graph_key: target.type for target in earlier.targets
    }
    return {
        target.type
        for target in later.targets
        if earlier_types.get(target.graph_key) == target.type
    }


def _should_contest(candidate: SupersessionCandidate) -> bool:
    return (
        bool(candidate.targets)
        and candidate.later.status is DecisionStatus.ACTIVE
        and _similarity(candidate.earlier.claim, candidate.later.claim)
        < CONFLICT_SIMILARITY
    )


def _context_ids(
    earlier: IntentBase,
    later: IntentBase,
    evidence_by_pr: Mapping[int, list[EvidenceItem]],
    *,
    limit: int = 24,
) -> tuple[str, ...]:
    ids: set[str] = set(earlier.evidence_ids) | set(later.evidence_ids)
    priority = sorted(ids)
    for pr_number in (earlier.pr_number, later.pr_number):
        ids.update(
            item.record_id for item in evidence_by_pr.get(pr_number, [])
        )
    return tuple((priority + sorted(ids - set(priority)))[:limit])


def _item_text(item: EvidenceItem) -> str:
    """Keep a PR/commit title even when a body is also present.

    ``EvidenceItem.text`` intentionally returns one primary field for
    extraction prompts. Supersession signals need both a PR title and its
    body, because either may contain "revert", "remove" or "deprecate".
    """
    parts: list[str] = []
    for key in ("title", "body", "content", "message"):
        value = item.payload.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if text not in parts:
                parts.append(text)
    patch = item.payload.get("patch")
    if isinstance(patch, str) and patch.strip():
        # Keep the diff visible to the judge without letting a huge patch
        # dominate the prompt. The citation still points at the whole record.
        parts.append(patch.strip()[:4000])
    return "\n".join(parts) if parts else item.text


def _evidence_lines(
    evidence_ids: Iterable[str],
    evidence_by_id: Mapping[str, EvidenceItem],
) -> tuple[tuple[str, str], ...]:
    lines: list[tuple[str, str]] = []
    for evidence_id in evidence_ids:
        item = evidence_by_id.get(evidence_id)
        if item is None:
            lines.append((evidence_id, ""))
            continue
        text = " ".join(_item_text(item).split())
        lines.append((evidence_id, text[:400]))
    return tuple(lines)


def _date_of(
    decision: IntentBase,
    pr_dates: dict[int, str],
    existing_dates: dict[int, str],
    existing_ids: set[str],
) -> str:
    source = existing_dates if decision.id in existing_ids else pr_dates
    value = source.get(decision.pr_number)
    return value.strip() if isinstance(value, str) else ""


def _similarity(left: str, right: str) -> float:
    from app.intent.validation.deduplicator import _tokens, jaccard

    return jaccard(_tokens(left), _tokens(right))


def _apply_intent_updates(
    intents: list[IntentBase],
    pending: Mapping[str, _StatusChange],
    decisions: list[DecisionIntent],
) -> list[IntentBase]:
    new_ids = {decision.id for decision in decisions}
    updated: list[IntentBase] = []
    for intent in intents:
        change = pending.get(intent.id)
        if change is not None and intent.id in new_ids:
            updated.append(
                intent.model_copy(
                    update={
                        "status": change.status,
                        "superseded_by": change.superseded_by,
                        "contested_by": change.contested_by,
                    }
                )
            )
        else:
            updated.append(intent)
    return updated


def _apply_reversal_links(
    intents: list[IntentBase],
    edges: list[SupersessionEdge],
) -> list[IntentBase]:
    reverses: dict[str, list[str]] = {}
    for edge in edges:
        if edge.rel_type == "REVERSES":
            reverses.setdefault(edge.start_id, []).append(edge.end_id)
    updated: list[IntentBase] = []
    for intent in intents:
        if isinstance(intent, ReversalIntent) and intent.id in reverses:
            updated.append(
                intent.model_copy(
                    update={
                        "reverses": sorted(
                            set(intent.reverses) | set(reverses[intent.id])
                        )
                    }
                )
            )
        else:
            updated.append(intent)
    return updated


__all__ = [
    "CONFLICT_SIMILARITY",
    "FAILURE_MARKERS",
    "REVERSAL_MARKERS",
    "REL_BY_RELATIONSHIP",
    "SIGNAL_CODE_REGION",
    "SIGNAL_CONFIG_REMOVAL",
    "SIGNAL_DOC_CHANGE",
    "SIGNAL_RETAINED_RATIONALE",
    "SIGNAL_REVERSAL_LANGUAGE",
    "SIGNAL_REVIEW_FAILED",
    "STATUS_BY_RELATIONSHIP",
    "SupersessionCandidate",
    "SupersessionDecision",
    "SupersessionDetector",
    "SupersessionEdge",
    "SupersessionResult",
    "StatusUpdate",
    "heuristic_classifier",
]
