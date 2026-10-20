"""Claim-level evidence grounding (Phase 4).

The schema gate proves that the model returned the right *shape*. It cannot
prove that the evidence says what the claim says. This module is the local,
deterministic firewall between those two facts:

* the claim is decomposed into atomic claims (sentence, semicolon and contrast
  boundaries — never conjunctions inside a single proposition);
* each atomic claim is checked against the text of the cited evidence, using
  stemmed content-term coverage rather than an opaque model call; a term shared
  by every citation (the module or product name, a shared preamble) cannot on
  its own link the claim to the evidence;
* citations are scored for relevance (precision), so an unrelated document
  cannot raise confidence;
* temporal validity is checked in both directions: the claimed ``valid_from``
  / ``valid_until`` dates must appear in evidence, and supporting evidence may
  not sit outside the asserted period;
* a reversal must be tied to a commit or pull request, not just a comment;
* explicit disagreement in a cited discussion is reported as conflict so the
  candidate can be marked contested instead of silently resolved.

This is deliberately a lexical firewall, not a natural-language inference
engine. It is conservative about *unsupported* claims: a claim that shares no
content with its evidence is rejected, and a borderline claim is sent to human
review. It never invents support that is not in the text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

from app.intent.selection.evidence_selector import EvidenceItem
from app.intent.validation.extraction_rules import REVERSAL_MARKERS

HUMAN_DISCUSSION = frozenset({"review", "review_comment", "issue_comment"})
WEAK_EVIDENCE_TYPES = frozenset({"review_comment"})
CHANGE_EVIDENCE_TYPES = frozenset({"commit", "pull_request"})

DEFAULT_SUPPORT_THRESHOLD = 0.20
DEFAULT_MIN_CLAIM_COVERAGE = 0.10
DEFAULT_MIN_RELEVANCE = 0.10

CONFLICT_MARKERS = re.compile(
    r"\b(disagree\w*|shouldn'?t|should not|do not think|don'?t think|"
    r"not convinced|wrong|incorrect|against|instead|however|"
    r"object\w*|concern\w*|revert this|i disagree)\b",
    re.IGNORECASE,
)

_STOPWORDS = frozenset(
    """
    a an the and or but if then than so to of in on at by for from with without
    into over under again further once here there when where why how all any
    both each few more most other some such no nor not only own same too very
    can could will just should now are was were be been being is am do does did
    doing have has had having i you he she it we they me him her us them my your
    his its our their this that these those as about against between during
    before after above below up down out off because while
    """.split()  # noqa: SIM905 - a word list is clearer than a 90-item literal
)
_WORD = re.compile(r"[A-Za-z0-9_]+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_CLAUSE = re.compile(r"[;]+|\s+(?:but|however|whereas|while|yet|although)\s+", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})")
_DATE_KEYS = (
    "merged_at",
    "committed_at",
    "submitted_at",
    "created_at",
    "updated_at",
)
_TEXT_KEYS = ("title", "body", "message", "content", "path", "patch")


@dataclass(frozen=True)
class GroundingIssue:
    code: str
    message: str
    claim: str = ""

    @property
    def text(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class EvidenceRelevance:
    evidence_id: str
    record_type: str
    relevance: float
    relevant: bool
    supports: bool


@dataclass(frozen=True)
class AtomicClaimSupport:
    claim: str
    supported: bool
    claim_coverage: float
    context_coverage: float
    matched_terms: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    reason: str = ""


@dataclass
class GroundingResult:
    ok: bool
    claims: list[AtomicClaimSupport] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    evidence: list[EvidenceRelevance] = field(default_factory=list)
    supporting_evidence_ids: list[str] = field(default_factory=list)
    irrelevant_evidence_ids: list[str] = field(default_factory=list)
    conflicting_evidence_ids: list[str] = field(default_factory=list)
    temporal_issues: list[str] = field(default_factory=list)
    reversal_issues: list[str] = field(default_factory=list)
    issues: list[GroundingIssue] = field(default_factory=list)

    @property
    def issue_texts(self) -> list[str]:
        return [issue.text for issue in self.issues]

    @property
    def citation_precision(self) -> float:
        if not self.evidence:
            return 0.0
        return sum(1 for item in self.evidence if item.relevant) / len(
            self.evidence
        )

    @property
    def temporal_valid(self) -> bool:
        return not self.temporal_issues

    @property
    def conflict(self) -> bool:
        return bool(self.conflicting_evidence_ids)

    @property
    def weak_only(self) -> bool:
        types = {
            item.record_type
            for item in self.evidence
            if item.supports and item.relevant and item.record_type
        }
        return bool(types) and types <= WEAK_EVIDENCE_TYPES


class ClaimGrounder:
    """Check whether the cited text can support each atomic claim."""

    def __init__(
        self,
        *,
        support_threshold: float = DEFAULT_SUPPORT_THRESHOLD,
        min_claim_coverage: float = DEFAULT_MIN_CLAIM_COVERAGE,
        min_relevance: float = DEFAULT_MIN_RELEVANCE,
    ) -> None:
        self.support_threshold = support_threshold
        self.min_claim_coverage = min_claim_coverage
        self.min_relevance = min_relevance

    def ground(
        self,
        claim: str,
        rationale: str,
        evidence_items: Iterable[EvidenceItem],
        *,
        target_paths: Iterable[str] = (),
        valid_from: str | None = None,
        valid_until: str | None = None,
        decision_type: str | None = None,
        reference_items: Iterable[EvidenceItem] = (),
    ) -> GroundingResult:
        items = list(evidence_items)
        references = [*items, *reference_items]
        paths = {str(path) for path in target_paths if path}

        atoms = decompose_claim(claim)
        rationale_terms = _terms(rationale)
        materialized = [
            (item, _evidence_text(item), _terms(_evidence_text(item)))
            for item in items
        ]
        evidence_terms = set().union(
            *(terms for _, _, terms in materialized)
        ) if materialized else set()
        term_counts: dict[str, int] = {}
        for _, _, terms in materialized:
            for term in terms:
                term_counts[term] = term_counts.get(term, 0) + 1
        # A term that appears in every cited artifact says nothing about *which*
        # artifact supports the claim (it is usually boilerplate: the module
        # name, the product name, a shared preamble). Such terms may contribute
        # to coverage but cannot on their own ground an atomic claim.
        evidence_count = len(materialized)
        distinctive_terms = {
            term
            for term in evidence_terms
            if evidence_count == 1 or term_counts.get(term, 0) < evidence_count
        }
        claim_terms = set().union(*(_terms(atom) for atom in atoms))
        context_terms = claim_terms | rationale_terms

        claims: list[AtomicClaimSupport] = []
        issues: list[GroundingIssue] = []
        unsupported: list[str] = []
        supporting_ids: set[str] = set()
        single_claim = len(atoms) == 1
        for atom in atoms:
            display = claim.strip() if single_claim else atom
            atom_terms = _terms(atom)
            atom_context = atom_terms | rationale_terms
            matched = sorted(atom_terms & evidence_terms)
            distinctive_matched = sorted(atom_terms & distinctive_terms)
            claim_coverage = _coverage(atom_terms, evidence_terms)
            context_coverage = _coverage(atom_context, evidence_terms)
            support_ids = tuple(
                item.record_id
                for item, _, item_terms in materialized
                if atom_terms & item_terms
            )
            reason = _support_reason(
                claim_terms=atom_terms,
                matched=matched,
                distinctive_matched=distinctive_matched,
                claim_coverage=claim_coverage,
                context_coverage=context_coverage,
                support_threshold=self.support_threshold,
                min_claim_coverage=self.min_claim_coverage,
            )
            supported = not reason
            if supported:
                supporting_ids.update(support_ids)
            else:
                unsupported.append(display)
                issues.append(
                    GroundingIssue("unsupported_claim", reason, claim=display)
                )
            claims.append(
                AtomicClaimSupport(
                    claim=display,
                    supported=supported,
                    claim_coverage=round(claim_coverage, 4),
                    context_coverage=round(context_coverage, 4),
                    matched_terms=tuple(matched),
                    evidence_ids=support_ids,
                    reason=reason,
                )
            )

        relevance, irrelevant = _relevance(
            materialized,
            context_terms=context_terms,
            target_paths=paths,
            min_relevance=self.min_relevance,
        )
        temporal_issues = _temporal_issues(
            items,
            references=references,
            supporting_ids=supporting_ids,
            valid_from=valid_from,
            valid_until=valid_until,
        )
        reversal_issues = _reversal_issues(
            claim=claim,
            rationale=rationale,
            evidence_items=items,
            decision_type=decision_type,
        )
        conflicts = _conflicts(items, context_terms=context_terms)

        for message in temporal_issues:
            issues.append(GroundingIssue("temporal", message))
        for message in reversal_issues:
            issues.append(GroundingIssue("reversal", message))

        return GroundingResult(
            ok=not unsupported and not temporal_issues and not reversal_issues,
            claims=claims,
            unsupported_claims=unsupported,
            evidence=relevance,
            supporting_evidence_ids=sorted(supporting_ids),
            irrelevant_evidence_ids=irrelevant,
            conflicting_evidence_ids=conflicts,
            temporal_issues=temporal_issues,
            reversal_issues=reversal_issues,
            issues=issues,
        )


def decompose_claim(claim: str) -> list[str]:
    """Split one claim into independently checkable atomic claims."""
    text = " ".join(claim.split())
    if not text:
        return []
    atoms: list[str] = []
    for sentence in _SENTENCE.split(text):
        for part in _CLAUSE.split(sentence):
            cleaned = part.strip(" .,;:!?")
            if cleaned:
                atoms.append(cleaned)
    return atoms or [text]


def _support_reason(
    *,
    claim_terms: set[str],
    matched: list[str],
    distinctive_matched: list[str],
    claim_coverage: float,
    context_coverage: float,
    support_threshold: float,
    min_claim_coverage: float,
) -> str:
    if not claim_terms:
        return "claim has no checkable content terms"
    if not matched:
        return "no content term in the claim appears in the cited evidence"
    if len(matched) == 1 and not distinctive_matched:
        return (
            "the only content term the claim shares with the evidence appears "
            "in every cited artifact"
        )
    if claim_coverage < min_claim_coverage:
        return (
            "the claim shares only "
            f"{claim_coverage:.0%} of its content terms with the evidence"
        )
    if context_coverage < support_threshold:
        return (
            "the claim and rationale share only "
            f"{context_coverage:.0%} of their content terms with the evidence"
        )
    return ""


def _relevance(
    materialized: list[tuple[EvidenceItem, str, set[str]]],
    *,
    context_terms: set[str],
    target_paths: set[str],
    min_relevance: float,
) -> tuple[list[EvidenceRelevance], list[str]]:
    relevance: list[EvidenceRelevance] = []
    irrelevant: list[str] = []
    for item, _text, terms in materialized:
        shared = context_terms & terms
        score = _coverage(context_terms, terms)
        path = item.payload.get("path")
        path_targeted = isinstance(path, str) and path in target_paths
        if item.record_type == "changed_file":
            # A diff is structural support for a targeted change even when its
            # code does not repeat the prose of the claim.
            relevant = bool(shared) or path_targeted
        else:
            relevant = bool(shared) and score >= min_relevance
        relevance.append(
            EvidenceRelevance(
                evidence_id=item.record_id,
                record_type=item.record_type,
                relevance=round(score, 4),
                relevant=relevant,
                supports=bool(shared),
            )
        )
        if not relevant:
            irrelevant.append(item.record_id)
    return relevance, irrelevant


def _temporal_issues(
    evidence_items: list[EvidenceItem],
    *,
    references: list[EvidenceItem],
    supporting_ids: set[str],
    valid_from: str | None,
    valid_until: str | None,
) -> list[str]:
    issues: list[str] = []
    start = _parse_date(valid_from) if valid_from else None
    end = _parse_date(valid_until) if valid_until else None

    if valid_from:
        if start is None:
            if not _date_text_present(valid_from, references):
                issues.append(
                    "temporal_date_not_in_evidence: valid_from "
                    f"{valid_from!r} is not a date in the evidence"
                )
        elif not _date_present(start, references):
            issues.append(
                "temporal_date_not_in_evidence: valid_from "
                f"{start.isoformat()} is not a date in the evidence"
            )

    if valid_until:
        if end is None:
            if not _date_text_present(valid_until, references):
                issues.append(
                    "temporal_date_not_in_evidence: valid_until "
                    f"{valid_until!r} is not a date in the evidence"
                )
        elif not _date_present(end, references):
            issues.append(
                "temporal_date_not_in_evidence: valid_until "
                f"{end.isoformat()} is not a date in the evidence"
            )

    supporting = [item for item in evidence_items if item.record_id in supporting_ids]
    support_dates = [
        day
        for item in supporting
        for day in _evidence_dates(item)
    ]
    if start is not None and support_dates and all(day > start for day in support_dates):
        issues.append(
            "temporal_support_after_valid_from: every supporting artifact "
            f"postdates {start.isoformat()}"
        )
    if end is not None and any(day > end for day in support_dates):
        issues.append(
            "temporal_support_after_valid_until: supporting artifact postdates "
            f"{end.isoformat()}"
        )
    return issues


def _reversal_issues(
    *,
    claim: str,
    rationale: str,
    evidence_items: list[EvidenceItem],
    decision_type: str | None,
) -> list[str]:
    if decision_type != "reversal":
        return []
    issues: list[str] = []
    if not any(item.record_type in CHANGE_EVIDENCE_TYPES for item in evidence_items):
        issues.append(
            "reversal_without_later_change: a reversal must cite a commit or "
            "pull request, not only discussion"
        )
    texts = [claim, rationale, *(_evidence_text(item) for item in evidence_items)]
    if not any(REVERSAL_MARKERS.search(text) for text in texts if text):
        issues.append(
            "reversal_without_reversal_language: the evidence does not state a "
            "revert, removal or rollback"
        )
    return issues


def _conflicts(
    evidence_items: list[EvidenceItem], *, context_terms: set[str]
) -> list[str]:
    conflicts: list[str] = []
    for item in evidence_items:
        if item.record_type not in HUMAN_DISCUSSION:
            continue
        text = _evidence_text(item)
        if CONFLICT_MARKERS.search(text) and (context_terms & _terms(text)):
            conflicts.append(item.record_id)
    return sorted(set(conflicts))


def _evidence_text(item: EvidenceItem) -> str:
    parts: list[str] = []
    base = item.text
    if base:
        parts.append(base)
    for key in _TEXT_KEYS:
        value = item.payload.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(dict.fromkeys(parts))


def _evidence_dates(item: EvidenceItem) -> set[date]:
    dates: set[date] = set()
    for key in _DATE_KEYS:
        parsed = _parse_date(item.payload.get(key))
        if parsed is not None:
            dates.add(parsed)
    for match in _ISO_DATE.finditer(_evidence_text(item)):
        parsed = _parse_date(match.group(1))
        if parsed is not None:
            dates.add(parsed)
    return dates


def _date_present(day: date, items: Iterable[EvidenceItem]) -> bool:
    return any(day in _evidence_dates(item) for item in items)


def _date_text_present(value: str, items: Iterable[EvidenceItem]) -> bool:
    stripped = value.strip()
    return bool(stripped) and any(
        stripped in _evidence_text(item) for item in items
    )


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    match = _ISO_DATE.search(value)
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _coverage(terms: set[str], pool: set[str]) -> float:
    if not terms:
        return 0.0
    return len(terms & pool) / len(terms)


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in _WORD.findall(text or ""):
        lowered = raw.lower()
        if lowered in _STOPWORDS:
            continue
        stemmed = _stem(lowered)
        if len(stemmed) >= 2:
            terms.add(stemmed)
    return terms


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith(("ies", "ied")):
        word = word[:-3] + "y"
    elif len(word) > 4 and word.endswith(("ses", "xes", "zes", "ches", "shes")):
        word = word[:-2]
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    for suffix in ("ing", "edly", "ed", "ly"):
        if len(word) > len(suffix) + 3 and word.endswith(suffix):
            word = word[: -len(suffix)]
            break
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


__all__ = [
    "AtomicClaimSupport",
    "CHANGE_EVIDENCE_TYPES",
    "CONFLICT_MARKERS",
    "ClaimGrounder",
    "EvidenceRelevance",
    "GroundingIssue",
    "GroundingResult",
    "HUMAN_DISCUSSION",
    "WEAK_EVIDENCE_TYPES",
    "decompose_claim",
]
