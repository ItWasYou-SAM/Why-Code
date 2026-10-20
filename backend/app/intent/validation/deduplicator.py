"""Duplicate detection, adjudication and merging (Phase 7).

Duplicate handling is deliberately staged:

1. **Normalisation.** Claims are lowercased, stripped of punctuation and
   whitespace-collapsed. An exact normalised match is always a candidate.
2. **Candidate generation.** Only same-family intents from different PRs that
   share a specific target (``Symbol``, ``ConfigKey`` or ``CodeRegion``) are
   compared. A deterministic hashed n-gram embedding supplies the similarity;
   it is a candidate generator, never proof of equivalence. An injectable
   :class:`EmbeddingModel` can replace it with a semantic embedding model.
3. **Adjudication.** One narrow LLM call answers whether the pair should be
   merged, kept separate, marked conflicting, or needs review.
4. **Deterministic application.** Code owns the canonical ID, the evidence
   union, the ``CONFLICTS_WITH`` edge and every lifecycle update. High-impact
   candidates are kept apart and queued for a human instead of auto-merging.

Merging only happens when the claims are semantically equivalent, the targets
overlap, the temporal scopes are compatible and the judge says the evidence can
be unioned without contradiction. A conflict never merges: both nodes survive
and receive a ``CONFLICTS_WITH`` edge plus a contested status.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from typing import Protocol

from app.intent.llm.schemas import (
    DuplicateRelationshipLiteral,
    DuplicateVerdict,
)
from app.intent.models import (
    DecisionStatus,
    DecisionType,
    IntentBase,
    ProposedParticipantRef,
    ResolvedTarget,
    TargetType,
)
from app.intent.selection.evidence_selector import EvidenceItem

_WORD = re.compile(r"[a-z0-9_]+")
_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[^\w\s]")
_NEGATION = re.compile(
    r"\b(not|never|no|none|cannot|can't|without|disable[sd]?|remov\w*|"
    r"drop(?:ped|s)?|must not|should not)\b",
    re.IGNORECASE,
)
_HIGH_IMPACT = re.compile(
    r"\b(security|auth(?:entication|orization)?|privacy|data loss|breaking|"
    r"backward|public api|contract|migration|deprecat\w*|payment|billing|"
    r"encrypt\w*|credential\w*)\b",
    re.IGNORECASE,
)
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "to", "of", "in",
    "on", "for", "and", "or", "by", "with", "that", "this", "it", "as",
    "at", "from", "we", "not", "no", "so", "if", "then", "than", "only",
}
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
MERGE_ELIGIBLE_STATUSES = {
    DecisionStatus.ACTIVE,
    DecisionStatus.UNCERTAIN,
}
MAX_CONTEXT_EVIDENCE = 24


class EmbeddingModel(Protocol):
    """The only thing candidate generation needs from an embedding model."""

    def embed(self, text: str) -> Sequence[float]: ...


class HashingEmbedder:
    """Deterministic offline embedding used when no semantic model is supplied.

    Features are word unigrams and character 3/4-grams hashed into a fixed
    vector. It is intentionally lexical: the LLM adjudicator is what decides
    semantic equivalence. Production callers can inject a semantic embedder
    without changing the rest of the pipeline.
    """

    def __init__(self, dimension: int = 384) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self.dimension = dimension

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        normalized = normalize_claim(text)
        words = [
            token
            for token in _WORD.findall(normalized)
            if token not in _STOPWORDS and len(token) > 1
        ]
        features: list[tuple[str, float]] = [
            (f"w:{word}", 2.0) for word in words
        ]
        compact = normalized.replace(" ", "")
        for size in (3, 4):
            for index in range(max(0, len(compact) - size + 1)):
                features.append((f"c:{compact[index:index + size]}", 1.0))
        for feature, weight in features:
            digest = hashlib.blake2b(
                feature.encode("utf-8"), digest_size=8
            ).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign * weight
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            return [value / norm for value in vector]
        return vector


@dataclass(frozen=True)
class DuplicatePair:
    """Backward-compatible, ID-only candidate pair."""

    earlier_id: str
    later_id: str
    similarity: float
    reason: str
    exact: bool = False
    target_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class DuplicateCandidate:
    """One pair that survived deterministic candidate generation."""

    earlier: IntentBase
    later: IntentBase
    similarity: float
    exact: bool
    temporal_compatible: bool
    target_keys: tuple[str, ...] = ()
    evidence_lines: tuple[tuple[str, str], ...] = ()
    context_evidence_ids: tuple[str, ...] = ()
    reason: str = ""

    @property
    def allowed_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                set(self.earlier.evidence_ids)
                | set(self.later.evidence_ids)
                | set(self.context_evidence_ids)
            )
        )


@dataclass(frozen=True)
class DuplicateMerge:
    """The canonical intent absorbed ``later_id``."""

    earlier_id: str
    later_id: str
    similarity: float
    exact: bool
    reason: str
    evidence_ids: tuple[str, ...] = ()
    target_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class DuplicateDecision:
    """Audit record for every adjudicated candidate."""

    earlier_id: str
    later_id: str
    relationship: str
    similarity: float
    exact: bool
    evidence_ids: tuple[str, ...]
    explanation: str
    target_keys: tuple[str, ...] = ()
    high_impact: bool = False
    applied: str = ""


@dataclass(frozen=True)
class ConflictEdge:
    """A later -> earlier ``CONFLICTS_WITH`` edge."""

    start_id: str
    end_id: str
    reason: str
    rel_type: str = "CONFLICTS_WITH"


@dataclass
class DuplicateResult:
    intents: list[IntentBase]
    merges: list[DuplicateMerge]
    decisions: list[DuplicateDecision]
    conflict_edges: list[ConflictEdge]


class Deduplicator:
    def __init__(
        self,
        *,
        threshold: float = 0.70,
        embedder: EmbeddingModel | None = None,
    ) -> None:
        if not 0 < threshold <= 1:
            raise ValueError("threshold must be in (0, 1]")
        self.threshold = threshold
        self.embedder = embedder or HashingEmbedder()

    def find(self, intents: list[IntentBase]) -> list[DuplicatePair]:
        """Return ID-only candidate pairs (used by tests and reports)."""
        pairs: list[DuplicatePair] = []
        for candidate in self.candidates(intents):
            pairs.append(
                DuplicatePair(
                    earlier_id=candidate.earlier.id,
                    later_id=candidate.later.id,
                    similarity=round(candidate.similarity, 3),
                    reason=candidate.reason,
                    exact=candidate.exact,
                    target_keys=candidate.target_keys,
                )
            )
        return pairs

    def candidates(
        self,
        intents: list[IntentBase],
        *,
        evidence_by_id: Mapping[str, EvidenceItem] | None = None,
        evidence_by_pr: Mapping[int, list[EvidenceItem]] | None = None,
    ) -> list[DuplicateCandidate]:
        evidence_by_id = evidence_by_id or {}
        evidence_by_pr = evidence_by_pr or {}
        ordered = sorted(intents, key=_sort_key)
        candidates: list[DuplicateCandidate] = []
        for earlier, later in combinations(ordered, 2):
            if not _pair_is_eligible(earlier, later):
                continue
            target_keys = _shared_specific_targets(earlier, later)
            if not target_keys:
                continue
            exact = normalize_claim(earlier.claim) == normalize_claim(later.claim)
            similarity = self.similarity(earlier.claim, later.claim)
            if not exact and similarity < self.threshold:
                continue
            reason = (
                "exact normalized claim over the same target(s) in different PRs"
                if exact
                else "near-identical claim over the same target(s) in different PRs"
            )
            evidence_ids, evidence_lines = _evidence_context(
                earlier,
                later,
                evidence_by_id,
                evidence_by_pr,
            )
            candidates.append(
                DuplicateCandidate(
                    earlier=earlier,
                    later=later,
                    similarity=round(similarity, 4),
                    exact=exact,
                    temporal_compatible=_temporal_compatible(earlier, later),
                    target_keys=target_keys,
                    evidence_lines=evidence_lines,
                    context_evidence_ids=evidence_ids,
                    reason=reason,
                )
            )
        return candidates

    def similarity(self, left: str, right: str) -> float:
        return cosine(self.embedder.embed(left), self.embedder.embed(right))

    def adjudicate(
        self,
        intents: list[IntentBase],
        *,
        evidence_by_id: Mapping[str, EvidenceItem] | None = None,
        evidence_by_pr: Mapping[int, list[EvidenceItem]] | None = None,
        classifier: Callable[[DuplicateCandidate], DuplicateVerdict] | None = None,
    ) -> DuplicateResult:
        current = {intent.id: intent for intent in intents}
        merges: list[DuplicateMerge] = []
        decisions: list[DuplicateDecision] = []
        conflict_edges: list[ConflictEdge] = []
        seen_conflicts: set[tuple[str, str]] = set()
        processed: set[tuple[str, str]] = set()

        # A merge changes the canonical evidence set, which can expose a new
        # candidate involving the survivor. Repeat until no pair merges; a
        # finite number of intents means this always terminates.
        while True:
            candidates = self.candidates(
                list(current.values()),
                evidence_by_id=evidence_by_id,
                evidence_by_pr=evidence_by_pr,
            )
            merged_this_round = False
            for original in candidates:
                pair = (original.earlier.id, original.later.id)
                if pair in processed:
                    continue
                earlier = current.get(original.earlier.id)
                later = current.get(original.later.id)
                if earlier is None or later is None:
                    continue
                processed.add(pair)
                candidate = _refresh_candidate(
                    original,
                    earlier,
                    later,
                    evidence_by_id=evidence_by_id or {},
                    evidence_by_pr=evidence_by_pr or {},
                )
                verdict = _safe_verdict(candidate, classifier, self.threshold)
                verdict = _validate_verdict(candidate, verdict)
                high_impact = _is_high_impact(earlier, later)
                relationship = verdict.relationship
                applied = "kept"
                explanation = verdict.explanation

                if relationship == "merge" and not candidate.temporal_compatible:
                    relationship = "keep_separate"
                    applied = "kept"
                    explanation = (
                        "temporal scopes are incompatible; the decisions were "
                        "kept apart"
                    )
                elif relationship == "merge" and high_impact:
                    applied = "review"
                    explanation = (
                        "high-impact duplicate merge requires human review; both "
                        "decisions were kept"
                    )
                    if later.duplicate_of is None:
                        later = later.model_copy(
                            update={"duplicate_of": earlier.id}
                        )
                        current[later.id] = later
                elif relationship == "merge":
                    merged = _merge_intents(earlier, later)
                    current[earlier.id] = merged
                    del current[later.id]
                    applied = "merged"
                    merged_this_round = True
                    merges.append(
                        DuplicateMerge(
                            earlier_id=earlier.id,
                            later_id=later.id,
                            similarity=candidate.similarity,
                            exact=candidate.exact,
                            reason=explanation,
                            evidence_ids=tuple(merged.evidence_ids),
                            target_keys=candidate.target_keys,
                        )
                    )
                elif relationship == "conflicts":
                    current[earlier.id] = _mark_contested(earlier, later)
                    current[later.id] = _mark_contested(later, earlier)
                    applied = "conflict"
                    key = (later.id, earlier.id)
                    if key not in seen_conflicts:
                        seen_conflicts.add(key)
                        conflict_edges.append(
                            ConflictEdge(
                                start_id=later.id,
                                end_id=earlier.id,
                                reason=explanation or "the evidence conflicts",
                            )
                        )
                elif relationship == "insufficient_evidence":
                    applied = "review"

                decisions.append(
                    DuplicateDecision(
                        earlier_id=earlier.id,
                        later_id=later.id,
                        relationship=relationship,
                        similarity=candidate.similarity,
                        exact=candidate.exact,
                        evidence_ids=tuple(verdict.evidence_ids),
                        explanation=explanation,
                        target_keys=candidate.target_keys,
                        high_impact=high_impact,
                        applied=applied,
                    )
                )
            if not merged_this_round:
                break

        return DuplicateResult(
            intents=sorted(current.values(), key=_sort_key),
            merges=merges,
            decisions=decisions,
            conflict_edges=conflict_edges,
        )


def normalize_claim(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace."""
    lowered = text.casefold()
    stripped = _PUNCTUATION.sub(" ", lowered)
    return _WHITESPACE.sub(" ", stripped).strip()


def cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def mark_duplicates(
    intents: list[IntentBase], pairs: list[DuplicatePair]
) -> tuple[list[IntentBase], list[DuplicatePair]]:
    """Legacy marker helper retained for callers outside the pipeline.

    The Phase 7 pipeline merges through :meth:`Deduplicator.adjudicate`
    instead of marking a later node and keeping both.
    """
    duplicate_of: dict[str, str] = {}
    for pair in pairs:
        duplicate_of.setdefault(pair.later_id, pair.earlier_id)
    updated: list[IntentBase] = []
    for intent in intents:
        marker = duplicate_of.get(intent.id)
        if marker is None:
            updated.append(intent)
        else:
            updated.append(intent.model_copy(update={"duplicate_of": marker}))
    return updated, pairs


def jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _WORD.findall(normalize_claim(text))
        if token not in _STOPWORDS and len(token) > 1
    }


def _pair_is_eligible(earlier: IntentBase, later: IntentBase) -> bool:
    if earlier.kind != later.kind:
        return False
    if earlier.pr_number == later.pr_number:
        return False
    if earlier.duplicate_of is not None or later.duplicate_of is not None:
        return False
    if earlier.status not in MERGE_ELIGIBLE_STATUSES:
        return False
    if later.status not in MERGE_ELIGIBLE_STATUSES:
        return False
    return earlier.id != later.id


def _shared_specific_targets(
    earlier: IntentBase, later: IntentBase
) -> tuple[str, ...]:
    earlier_keys = {
        target.graph_key: target.type
        for target in earlier.targets
        if target.type in SPECIFIC_TARGETS
    }
    later_keys = {
        target.graph_key: target.type
        for target in later.targets
        if target.type in SPECIFIC_TARGETS
    }
    return tuple(
        sorted(
            key
            for key in earlier_keys.keys() & later_keys.keys()
            if earlier_keys[key] == later_keys[key]
        )
    )


def _temporal_compatible(earlier: IntentBase, later: IntentBase) -> bool:
    left = earlier.temporal_scope
    right = later.temporal_scope
    if left is None or right is None:
        return True
    try:
        left_from = _parse_date(left.valid_from)
        left_until = _parse_date(left.valid_until) if left.valid_until else None
        right_from = _parse_date(right.valid_from)
        right_until = _parse_date(right.valid_until) if right.valid_until else None
    except ValueError:
        return False
    if left_until is not None and right_from > left_until:
        return False
    return not (right_until is not None and left_from > right_until)


def _parse_date(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _evidence_context(
    earlier: IntentBase,
    later: IntentBase,
    evidence_by_id: Mapping[str, EvidenceItem],
    evidence_by_pr: Mapping[int, list[EvidenceItem]],
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    ids: list[str] = []
    for evidence_id in (*earlier.evidence_ids, *later.evidence_ids):
        if evidence_id not in ids:
            ids.append(evidence_id)
    for pr_number in (earlier.pr_number, later.pr_number):
        for item in evidence_by_pr.get(pr_number, []):
            if item.record_id not in ids:
                ids.append(item.record_id)
    ids = ids[:MAX_CONTEXT_EVIDENCE]
    lines: list[tuple[str, str]] = []
    for evidence_id in ids:
        entry = evidence_by_id.get(evidence_id)
        if entry is None:
            lines.append((evidence_id, ""))
            continue
        text = " ".join(_item_text(entry).split())
        lines.append((evidence_id, text[:500]))
    return tuple(ids), tuple(lines)


def _refresh_candidate(
    candidate: DuplicateCandidate,
    earlier: IntentBase,
    later: IntentBase,
    *,
    evidence_by_id: Mapping[str, EvidenceItem],
    evidence_by_pr: Mapping[int, list[EvidenceItem]],
) -> DuplicateCandidate:
    if earlier is candidate.earlier and later is candidate.later:
        return candidate
    evidence_ids, evidence_lines = _evidence_context(
        earlier, later, evidence_by_id, evidence_by_pr
    )
    return DuplicateCandidate(
        earlier=earlier,
        later=later,
        similarity=candidate.similarity,
        exact=candidate.exact,
        temporal_compatible=_temporal_compatible(earlier, later),
        target_keys=_shared_specific_targets(earlier, later),
        evidence_lines=evidence_lines,
        context_evidence_ids=evidence_ids,
        reason=candidate.reason,
    )


def _item_text(item: EvidenceItem) -> str:
    parts: list[str] = []
    for key in ("title", "body", "content", "message"):
        value = item.payload.get(key)
        if isinstance(value, str) and value.strip():
            text = value.strip()
            if text not in parts:
                parts.append(text)
    patch = item.payload.get("patch")
    if isinstance(patch, str) and patch.strip():
        parts.append(patch.strip()[:2000])
    return "\n".join(parts) if parts else item.text


def _safe_verdict(
    candidate: DuplicateCandidate,
    classifier: Callable[[DuplicateCandidate], DuplicateVerdict] | None,
    threshold: float,
) -> DuplicateVerdict:
    if classifier is None:
        return _fallback_verdict(candidate, threshold)
    try:
        return classifier(candidate)
    except Exception as error:  # noqa: BLE001 - one bad verdict never stops a run
        return _fallback_verdict(
            candidate,
            threshold,
            f"the duplicate judge failed: {type(error).__name__}",
            force_insufficient=True,
        )


def _validate_verdict(
    candidate: DuplicateCandidate, verdict: DuplicateVerdict
) -> DuplicateVerdict:
    if (
        verdict.earlier_intent_id != candidate.earlier.id
        or verdict.later_intent_id != candidate.later.id
    ):
        return _fallback_verdict(
            candidate,
            1.0,
            "the verdict does not name this earlier/later pair",
            force_insufficient=True,
        )
    allowed = set(candidate.allowed_evidence_ids)
    if allowed and not set(verdict.evidence_ids) <= allowed:
        return _fallback_verdict(
            candidate,
            1.0,
            "the verdict cites evidence outside the candidate bundle",
            force_insufficient=True,
        )
    return verdict


def _fallback_verdict(
    candidate: DuplicateCandidate,
    threshold: float,
    explanation: str = "",
    *,
    force_insufficient: bool = False,
) -> DuplicateVerdict:
    relationship: DuplicateRelationshipLiteral
    if force_insufficient:
        relationship = "insufficient_evidence"
    elif candidate.exact:
        relationship = "merge"
    elif _claims_conflict(candidate.earlier.claim, candidate.later.claim):
        relationship = "conflicts"
    elif candidate.temporal_compatible and candidate.similarity >= threshold:
        relationship = "merge"
    else:
        relationship = "keep_separate"
    evidence = [
        *candidate.earlier.evidence_ids,
        *candidate.later.evidence_ids,
        *candidate.context_evidence_ids,
    ]
    explanation = explanation or (
        "deterministic fallback: "
        + (
            "exact normalized claim"
            if candidate.exact
            else f"embedding similarity {candidate.similarity:.3f}"
        )
    )
    return DuplicateVerdict(
        earlier_intent_id=candidate.earlier.id,
        later_intent_id=candidate.later.id,
        relationship=relationship,
        evidence_ids=[evidence[0]] if evidence else ["unknown"],
        explanation=explanation,
    )


def _claims_conflict(left: str, right: str) -> bool:
    return bool(_NEGATION.search(left)) != bool(_NEGATION.search(right))


def _is_high_impact(earlier: IntentBase, later: IntentBase) -> bool:
    for intent in (earlier, later):
        if intent.decision_type in {
            DecisionType.API_CONTRACT,
            DecisionType.OPERATIONAL_POLICY,
        }:
            return True
        text = f"{intent.claim} {intent.rationale}"
        if _HIGH_IMPACT.search(text):
            return True
    return False


def _mark_contested(intent: IntentBase, other: IntentBase) -> IntentBase:
    if intent.status in TERMINAL_STATUSES:
        return intent
    update: dict[str, object] = {"status": DecisionStatus.CONTESTED}
    if intent.contested_by is None:
        update["contested_by"] = other.id
    return intent.model_copy(update=update)


def _merge_intents(earlier: IntentBase, later: IntentBase) -> IntentBase:
    merged_from = list(
        dict.fromkeys(
            [*earlier.merged_from, later.id, *later.merged_from]
        )
    )
    participants: list[ProposedParticipantRef] = []
    seen_participants: set[tuple[str, str]] = set()
    for participant in (*earlier.participants_proposed, *later.participants_proposed):
        key = (participant.login, participant.role.value)
        if key in seen_participants:
            continue
        seen_participants.add(key)
        participants.append(participant)
    update: dict[str, object] = {
        "evidence_ids": sorted(
            dict.fromkeys([*earlier.evidence_ids, *later.evidence_ids])
        ),
        "targets": _merge_targets(earlier.targets, later.targets),
        "produced_in": sorted(
            dict.fromkeys([*earlier.produced_in, *later.produced_in])
        ),
        "missing_evidence": sorted(
            dict.fromkeys([*earlier.missing_evidence, *later.missing_evidence])
        ),
        "confidence_reasons": sorted(
            dict.fromkeys(
                [*earlier.confidence_reasons, *later.confidence_reasons]
            )
        ),
        "participants_proposed": participants,
        "merged_from": merged_from,
    }
    if not earlier.rationale and later.rationale:
        update["rationale"] = later.rationale
    if earlier.temporal_scope is None and later.temporal_scope is not None:
        update["temporal_scope"] = later.temporal_scope
    return earlier.model_copy(update=update)


def _merge_targets(
    earlier: list[ResolvedTarget], later: list[ResolvedTarget]
) -> list[ResolvedTarget]:
    merged: dict[str, ResolvedTarget] = {}
    for target in (*earlier, *later):
        merged.setdefault(target.graph_key, target)
    return list(merged.values())


def _sort_key(intent: IntentBase) -> tuple[int, str]:
    return intent.pr_number, intent.id


__all__ = [
    "ConflictEdge",
    "Deduplicator",
    "DuplicateCandidate",
    "DuplicateDecision",
    "DuplicateMerge",
    "DuplicatePair",
    "DuplicateResult",
    "EmbeddingModel",
    "HashingEmbedder",
    "cosine",
    "jaccard",
    "mark_duplicates",
    "normalize_claim",
]
