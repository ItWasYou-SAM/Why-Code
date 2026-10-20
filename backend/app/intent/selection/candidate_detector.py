"""Deterministic candidate detection.

No PR reaches the LLM merely because it is merged. Signals are additive and
each point has a reason and (where possible) a source evidence ID. A single
PR may produce several candidates; each is extracted independently, so a
reversal cannot smuggle in an unsupported constraint (D-0039).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from app.intent.selection.evidence_selector import EvidenceItem, PrEvidenceBundle

DECISION_SCORE_THRESHOLD = 4

HIGH_VALUE_PATTERNS = (
    "_client",
    "_config",
    "_auth",
    "_models",
    "_transports",
    "transport",
    "retry",
    "timeout",
    "redirect",
    "limits",
    "middleware",
    "pyproject.toml",
    "setup.cfg",
    "settings",
    "config",
    "defaults",
)

DOC_MARKERS = ("/docs/", "docs/", "adr", "architecture", "readme")
CONFIG_SUFFIXES = (".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".conf")

REVERSAL_TITLE = re.compile(
    r"\b(revert|reverts|reverted|remove|removes|removed|deprecat\w*|"
    r"rollback|roll back|drop|drops|replace|replaces)\b",
    re.IGNORECASE,
)
MOTIVATION_MARKERS = (
    "because",
    "in order to",
    "so that",
    "this fixes",
    "this prevents",
    "motivation",
    "the reason",
    "to avoid",
    "to prevent",
    "rationale",
    "why ",
)
RATIONALE_MARKERS = ("because", "fix", "prevent", "avoid", "require")
CONSTRAINT_MARKERS = re.compile(
    r"\b(must not|must|never|cannot|can't|only|require[sd]?|forbid\w*|"
    r"at most|at least|no more than|limited to|restrict\w*|ensure)\b",
    re.IGNORECASE,
)
INCIDENT_MARKERS = re.compile(
    r"\b(bug|regression|crash|incident|failure|fails|timeout|race|deadlock|"
    r"memory leak|security|vulnerab\w*|hang|corrupt\w*)\b",
    re.IGNORECASE,
)


class CandidateClass(StrEnum):
    DECISION_CANDIDATE = "DECISION_CANDIDATE"
    REVERSAL_CANDIDATE = "REVERSAL_CANDIDATE"
    CONSTRAINT_CANDIDATE = "CONSTRAINT_CANDIDATE"
    INCIDENT_CANDIDATE = "INCIDENT_CANDIDATE"
    LOW_SIGNAL = "LOW_SIGNAL"


@dataclass(frozen=True)
class Signal:
    name: str
    weight: int
    reason: str
    evidence_ids: tuple[str, ...] = ()


@dataclass
class Candidate:
    candidate_class: CandidateClass
    score: int
    signals: list[Signal] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def evidence_ids(self) -> list[str]:
        return sorted(
            {
                evidence_id
                for signal in self.signals
                for evidence_id in signal.evidence_ids
            }
        )

    @property
    def actionable(self) -> bool:
        return self.candidate_class is not CandidateClass.LOW_SIGNAL

    def as_dict(self) -> dict[str, object]:
        return {
            "candidate_class": self.candidate_class.value,
            "score": self.score,
            "reasons": list(self.reasons),
            "signals": [
                {
                    "name": signal.name,
                    "weight": signal.weight,
                    "reason": signal.reason,
                    "evidence_ids": list(signal.evidence_ids),
                }
                for signal in self.signals
            ],
        }


class CandidateDetector:
    def __init__(self, *, decision_threshold: int = DECISION_SCORE_THRESHOLD) -> None:
        self.decision_threshold = decision_threshold

    def detect(self, bundle: PrEvidenceBundle) -> list[Candidate]:
        signals: list[Signal] = []
        pr_id = bundle.pr.record_id

        # 1. Accepted change.
        signals.append(Signal("merged_pr", 2, "Merged PR", (pr_id,)))

        # 2. Multiple participants in review.
        participants = sorted(bundle.review_participants())
        if len(participants) >= 2:
            evidence = sorted(
                {
                    item.record_id
                    for item in [
                        *bundle.reviews,
                        *bundle.review_comments,
                        *bundle.issue_comments,
                    ]
                    if item.author in participants
                }
            )
            signals.append(
                Signal(
                    "multiple_review_participants",
                    3,
                    f"{len(participants)} review participants: "
                    f"{', '.join(participants)}",
                    tuple(evidence),
                )
            )

        # 3. Review comments anchored to changed lines.
        changed = bundle.changed_paths()
        anchored = [
            item
            for item in bundle.review_comments
            if item.payload.get("path") in changed
            and (
                isinstance(item.payload.get("line"), int)
                or isinstance(item.payload.get("start_line"), int)
            )
        ]
        if anchored:
            signals.append(
                Signal(
                    "line_anchored_review_comment",
                    4,
                    f"{len(anchored)} review comment(s) on changed lines",
                    tuple(item.record_id for item in anchored),
                )
            )

        # 4. Linked issue.
        if bundle.issues:
            signals.append(
                Signal(
                    "linked_issue",
                    3,
                    f"{len(bundle.issues)} linked issue(s)",
                    tuple(item.record_id for item in bundle.issues),
                )
            )

        # 5. Issue discussion: any human comment attached to the linked
        # issue is discussion, even a single maintainer reply.
        issue_authors = bundle.issue_discussion_authors()
        if bundle.issue_comments:
            signals.append(
                Signal(
                    "issue_discussion",
                    3,
                    f"{len(bundle.issue_comments)} issue comment(s) across "
                    f"{len(issue_authors)} author(s)",
                    tuple(item.record_id for item in bundle.issue_comments),
                )
            )

        # 6. PR body explains motivation.
        body = _text(bundle.pr.payload.get("body"))
        if len(body) >= 80 and _contains_any(body, MOTIVATION_MARKERS):
            signals.append(
                Signal(
                    "pr_body_motivation",
                    2,
                    "PR body states a motivation",
                    (pr_id,),
                )
            )

        # 7. Commit messages contain rationale language.
        rationale_commits = [
            item
            for item in bundle.commits
            if _contains_any(_text(item.payload.get("message")), RATIONALE_MARKERS)
        ]
        if rationale_commits:
            signals.append(
                Signal(
                    "commit_rationale",
                    2,
                    f"{len(rationale_commits)} commit(s) state a rationale",
                    tuple(item.record_id for item in rationale_commits),
                )
            )

        # 8. High-value subsystem changed.
        high_value = [
            item
            for item in bundle.files
            if _is_high_value(str(item.payload.get("path") or ""))
        ]
        if high_value:
            signals.append(
                Signal(
                    "high_value_path",
                    3,
                    f"{len(high_value)} high-value file(s) changed",
                    tuple(item.record_id for item in high_value),
                )
            )

        # 9. Docs / ADR changed in the same PR.
        docs = [
            item
            for item in bundle.files
            if _is_doc(str(item.payload.get("path") or ""))
        ]
        if docs or bundle.docs:
            doc_evidence = tuple(
                item.record_id for item in [*docs, *bundle.docs]
            )
            signals.append(
                Signal(
                    "docs_or_adr_changed",
                    4,
                    "Documentation or ADR changed with the code",
                    doc_evidence,
                )
            )

        # 10. Supersession candidate.
        title = _text(bundle.pr.payload.get("title"))
        title_match = REVERSAL_TITLE.search(title)
        if title_match:
            signals.append(
                Signal(
                    "reversal_title",
                    4,
                    f"PR title contains reversal language {title_match.group(0)!r}",
                    (pr_id,),
                )
            )

        score = sum(signal.weight for signal in signals)
        classes: list[CandidateClass] = []

        if score >= self.decision_threshold:
            classes.append(CandidateClass.DECISION_CANDIDATE)

        if _is_constraint_candidate(bundle, docs):
            classes.append(CandidateClass.CONSTRAINT_CANDIDATE)

        if title_match or _is_reversal_body(bundle):
            classes.append(CandidateClass.REVERSAL_CANDIDATE)

        if _is_incident_candidate(bundle):
            classes.append(CandidateClass.INCIDENT_CANDIDATE)

        if not classes:
            return [
                Candidate(
                    candidate_class=CandidateClass.LOW_SIGNAL,
                    score=score,
                    signals=signals,
                    reasons=[signal.reason for signal in signals]
                    or ["no recognized intent signal"],
                )
            ]

        return [
            Candidate(
                candidate_class=candidate_class,
                score=score,
                signals=signals,
                reasons=[signal.reason for signal in signals],
            )
            for candidate_class in classes
        ]


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _contains_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in markers)


def _is_high_value(path: str) -> bool:
    lowered = path.lower()
    return any(pattern in lowered for pattern in HIGH_VALUE_PATTERNS)


def _is_doc(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in DOC_MARKERS) or lowered.endswith(
        (".md", ".rst", ".adoc")
    )


def _is_config(path: str) -> bool:
    return path.lower().endswith(CONFIG_SUFFIXES)


def _is_constraint_candidate(
    bundle: PrEvidenceBundle, docs: list[EvidenceItem]
) -> bool:
    config_files = [
        item for item in bundle.files if _is_config(str(item.payload.get("path") or ""))
    ]
    if docs or bundle.docs or config_files:
        return True
    text = "\n".join(
        _text(item.payload.get("body"))
        for item in [bundle.pr, *bundle.reviews, *bundle.review_comments]
    )
    text += "\n" + "\n".join(_text(item.payload.get("message")) for item in bundle.commits)
    text += "\n" + "\n".join(_text(item.payload.get("content")) for item in bundle.docs)
    return bool(CONSTRAINT_MARKERS.search(text))


def _is_reversal_body(bundle: PrEvidenceBundle) -> bool:
    text = "\n".join(
        [
            _text(bundle.pr.payload.get("body")),
            *(_text(item.payload.get("message")) for item in bundle.commits),
            *(_text(item.payload.get("content")) for item in bundle.docs),
        ]
    )
    return bool(REVERSAL_TITLE.search(text))


def _is_incident_candidate(bundle: PrEvidenceBundle) -> bool:
    for issue in bundle.issues:
        text = " ".join(
            [
                _text(issue.payload.get("title")),
                _text(issue.payload.get("body")),
            ]
        )
        labels = issue.payload.get("labels")
        if isinstance(labels, list):
            text += " " + " ".join(str(label) for label in labels)
        if INCIDENT_MARKERS.search(text):
            return True
    return any(
        INCIDENT_MARKERS.search(_text(comment.payload.get("body")))
        for comment in bundle.issue_comments
        if comment.payload.get("issue_number") is not None
    )


__all__ = [
    "Candidate",
    "CandidateClass",
    "CandidateDetector",
    "DECISION_SCORE_THRESHOLD",
    "Signal",
]
