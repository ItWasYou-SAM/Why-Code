"""RelevanceScorer: deterministic, explainable PR triage.

Scores are intentionally simple and additive so every point can be traced
to a reason string. The band threshold is a module constant because tests
and the exporter both need it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

HIGH_SIGNAL_SCORE = 0.5
MEDIUM_SIGNAL_SCORE = 0.3

_CONFIG_SUFFIXES = (".yaml", ".yml", ".toml", ".ini", ".cfg", ".env")
_CONFIG_HINTS = ("config", "settings", "defaults", ".env")
_DOC_SUFFIXES = (".md", ".rst", ".txt")
_DECISION_WORDS = (
    "default",
    "retry",
    "timeout",
    "deprecat",
    "breaking",
    "policy",
    "limit",
    "redirect",
    "security",
    "feature flag",
)


@dataclass(frozen=True)
class ScoreResult:
    score: float
    band: str
    reasons: list[str] = field(default_factory=list)


def _is_config(path: str) -> bool:
    lowered = path.lower()
    return lowered.endswith(_CONFIG_SUFFIXES) or any(
        hint in lowered for hint in _CONFIG_HINTS
    )


def _is_doc(path: str) -> bool:
    return path.lower().endswith(_DOC_SUFFIXES)


def _is_test(path: str) -> bool:
    lowered = path.lower()
    return "/test" in lowered or lowered.startswith("test") or "_test." in lowered


class RelevanceScorer:
    def score(self, bundle: dict[str, Any]) -> ScoreResult:
        pr = bundle.get("pr") or {}
        files = bundle.get("files") or []
        commits = bundle.get("commits") or []
        reviews = bundle.get("reviews") or []
        review_comments = bundle.get("review_comments") or []
        issue_comments = bundle.get("issue_comments") or []

        score = 0.0
        reasons: list[str] = []

        code_files = [
            f for f in files if not _is_doc(str(f.get("filename", "")))
            and not _is_test(str(f.get("filename", "")))
        ]
        if code_files:
            score += 0.25
            reasons.append(f"{len(code_files)} non-doc source file(s) changed")

        config_files = [f for f in files if _is_config(str(f.get("filename", "")))]
        if config_files:
            score += 0.2
            reasons.append(f"{len(config_files)} config file(s) changed")

        if review_comments:
            score += 0.2
            reasons.append(f"{len(review_comments)} line-anchored review comment(s)")
        elif reviews:
            score += 0.1
            reasons.append(f"{len(reviews)} review(s)")

        if issue_comments:
            score += 0.1
            reasons.append(f"{len(issue_comments)} discussion comment(s)")

        if len(commits) >= 2:
            score += 0.1
            reasons.append(f"{len(commits)} commits")

        text = " ".join(
            str(part)
            for part in (
                pr.get("title", ""),
                pr.get("body", ""),
                *(c.get("message", "") for c in commits),
            )
        ).lower()
        matched = sorted({word for word in _DECISION_WORDS if word in text})
        if matched:
            score += min(0.15, 0.05 * len(matched))
            reasons.append(f"decision language: {', '.join(matched)}")

        if files and not code_files and not config_files:
            score = min(score, 0.2)
            reasons.append("documentation-only change")

        score = round(min(1.0, score), 3)
        if score >= HIGH_SIGNAL_SCORE:
            band = "high"
        elif score >= MEDIUM_SIGNAL_SCORE:
            band = "medium"
        else:
            band = "low"
        if not reasons:
            reasons.append("no recognized decision signal")
        return ScoreResult(score=score, band=band, reasons=reasons)
