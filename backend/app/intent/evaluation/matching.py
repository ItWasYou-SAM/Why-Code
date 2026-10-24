"""Deterministic matching between golden cases and a run's decisions.

No model is involved here. A pair is matched when it comes from an expected PR
and shares enough evidence, targets and claim vocabulary. The scoring is
intentionally conservative: a prediction with no evidence and no target overlap
can never absorb a golden case on prose similarity alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.intent.evaluation.models import (
    GoldenCase,
    PredictedDecision,
    PredictedTarget,
)

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "because",
        "been",
        "being",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "must",
        "not",
        "of",
        "on",
        "or",
        "our",
        "should",
        "so",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "which",
        "while",
        "why",
        "will",
        "with",
        "would",
    }
)
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class CaseMatch:
    case: GoldenCase
    decision: PredictedDecision
    score: float


@dataclass
class MatchResult:
    matches: list[CaseMatch] = field(default_factory=list)
    unmatched: list[PredictedDecision] = field(default_factory=list)

    def by_case(self) -> dict[str, CaseMatch]:
        return {match.case.case_id: match for match in self.matches}


def match_cases(
    cases: list[GoldenCase],
    decisions: list[PredictedDecision],
    *,
    threshold: float = 0.35,
) -> MatchResult:
    """Greedy one-to-one assignment, highest score first.

    Refusal cases never match a decision: a prediction emitted for a PR where
    the golden annotator found no decision is a false positive, not a match.
    """

    eligible = [case for case in cases if case.expected_status != "refused"]
    ranked: list[tuple[float, str, str]] = []
    for case in eligible:
        for decision in decisions:
            score = pair_score(case, decision)
            if score >= threshold:
                ranked.append((score, case.case_id, decision.id))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))

    by_case = {case.case_id: case for case in eligible}
    by_decision = {decision.id: decision for decision in decisions}
    used_cases: set[str] = set()
    used_decisions: set[str] = set()
    matches: list[CaseMatch] = []
    for score, case_id, decision_id in ranked:
        if case_id in used_cases or decision_id in used_decisions:
            continue
        used_cases.add(case_id)
        used_decisions.add(decision_id)
        matches.append(
            CaseMatch(case=by_case[case_id], decision=by_decision[decision_id], score=score)
        )
    matches.sort(key=lambda match: match.case.case_id)
    unmatched = [
        decision for decision in decisions if decision.id not in used_decisions
    ]
    return MatchResult(matches=matches, unmatched=unmatched)


def pair_score(case: GoldenCase, decision: PredictedDecision) -> float:
    pr_score = 0.0
    if case.expected_prs:
        if decision.pr_number is None:
            pr_score = 0.1
        elif decision.pr_number in case.expected_prs:
            pr_score = 1.0
        else:
            return 0.0

    expected_targets = {normalize_target_key(value) for value in case.expected_targets}
    predicted_targets = {
        normalize_target_key(target.as_key()) for target in decision.targets
    }
    target_recall = _recall(expected_targets, predicted_targets)
    evidence_recall = _recall(set(case.expected_evidence), set(decision.evidence_ids))
    claim_similarity = claim_overlap(
        case.expected_decision or "", decision.claim
    )
    if target_recall == 0.0 and evidence_recall == 0.0 and claim_similarity < 0.20:
        return 0.0
    return round(
        0.20 * pr_score
        + 0.30 * evidence_recall
        + 0.25 * target_recall
        + 0.25 * claim_similarity,
        6,
    )


def normalize_target_key(value: str) -> str:
    """Normalise ``type:identifier`` for comparison, not for storage."""

    kind, separator, identifier = value.partition(":")
    if not separator:
        return value.strip().lower()
    kind = kind.strip().lower()
    identifier = identifier.strip().replace("\\", "/")
    if kind == "file":
        identifier = identifier.lstrip("./")
    elif kind == "config_key":
        identifier = identifier.lower()
        for prefix in ("httpx.", "httpcore."):
            if identifier.startswith(prefix):
                identifier = identifier[len(prefix) :]
                break
    elif kind == "symbol":
        identifier = identifier.lower()
    return f"{kind}:{identifier}"


def target_key(target: PredictedTarget) -> str:
    return normalize_target_key(target.as_key())


def claim_overlap(left: str, right: str) -> float:
    left_tokens = _claim_tokens(left)
    right_tokens = _claim_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _claim_tokens(text: str) -> set[str]:
    return {
        _stem(token)
        for token in _WORD.findall(text.lower())
        if len(token) > 2 and token not in _STOPWORDS
    }


def _stem(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return f"{token[:-3]}y"
    if token.endswith("s") and len(token) > 4 and not token.endswith("ss"):
        return token[:-1]
    return token


def _recall(expected: set[str], predicted: set[str]) -> float:
    if not expected:
        return 0.0
    return len(expected & predicted) / len(expected)


__all__ = [
    "CaseMatch",
    "MatchResult",
    "claim_overlap",
    "match_cases",
    "normalize_target_key",
    "pair_score",
    "target_key",
]
