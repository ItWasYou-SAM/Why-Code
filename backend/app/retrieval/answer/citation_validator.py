"""Final anti-hallucination gate.

Structured output constrains shape, not truth. This validator resolves every
citation against the exact pack, checks that each cited evidence item belongs
to a selected decision, splits the answer into atomic claims, requires every
factual claim to carry at least one resolved evidence ID, verifies quoted text
against the excerpts the model saw, and checks specific code, people, PR, issue
and date details against the evidence cited by the same claim.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.retrieval.config import RetrievalConfig
from app.retrieval.context.evidence_packer import EvidencePack
from app.retrieval.models import (
    AnswerConfidence,
    AnswerDraft,
    AnswerStatus,
    EvidenceItem,
    ValidationResult,
)

_INLINE = re.compile(r"\[([^\]\n]{1,120})\]")
_QUOTE = re.compile(r'"([^"\n]{8,400})"|“([^”\n]{8,400})”')
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\[(\"“])")
_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")
_FILE = re.compile(
    r"\b[\w./-]+\.(?:py|pyi|md|rst|toml|cfg|ini|json|ya?ml|txt|go|rs|js|jsx|ts|tsx|java|rb|sh)\b",
    re.IGNORECASE,
)
_REFERENCE = re.compile(
    r"\b(?:PR|pull request|issue)\s*#?(\d+)\b|#(\d+)\b",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_SHA = re.compile(r"\b[0-9a-f]{7,40}\b")
_PERSON_BY = re.compile(
    r"\b(?:by|reviewer|author)\s+([A-Z][A-Za-z0-9_.-]{1,40})"
)
_PERSON_ACTION = re.compile(
    r"\b([A-Z][a-z]{2,})\s+"
    r"(?:wrote|said|reviewed|approved|committed|authored|commented|"
    r"proposed|argued|noted)\b"
)

_CONFIDENCE_ORDER = {
    AnswerConfidence.NONE: 0,
    AnswerConfidence.LOW: 1,
    AnswerConfidence.MEDIUM: 2,
    AnswerConfidence.HIGH: 3,
}
_BY_ORDER = {
    0: AnswerConfidence.NONE,
    1: AnswerConfidence.LOW,
    2: AnswerConfidence.MEDIUM,
    3: AnswerConfidence.HIGH,
}


class CitationValidator:
    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig()

    def validate(self, draft: AnswerDraft, pack: EvidencePack) -> ValidationResult:
        if draft.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE:
            return ValidationResult(
                answer_status=draft.answer_status,
                citation_valid=True,
                final_confidence=AnswerConfidence.NONE,
            )

        evidence_by_id = {item.evidence_id: item for item in pack.evidence}
        selected_decisions = {item.decision_id for item in pack.decisions}
        declared = list(draft.citations)
        inline = _tokens(draft.answer)
        invalid: list[str] = []
        valid_ids: list[str] = []
        for token in [*declared, *inline]:
            resolved = pack.resolve_citation(token)
            if resolved is None or resolved not in evidence_by_id:
                cleaned = token.strip().strip("[]")
                if cleaned and cleaned not in invalid:
                    invalid.append(cleaned)
                continue
            if not _belongs_to_selection(
                evidence_by_id[resolved], selected_decisions
            ):
                cleaned = token.strip().strip("[]")
                if cleaned and cleaned not in invalid:
                    invalid.append(cleaned)
                continue
            if resolved not in valid_ids:
                valid_ids.append(resolved)

        claims = _claims(draft.answer)
        unsupported: list[str] = []
        fabricated: list[str] = []
        for claim in claims:
            if not _is_factual(claim):
                continue
            resolved_ids = _resolved(_tokens(claim), pack, evidence_by_id)
            if not resolved_ids:
                unsupported.append(claim)
                continue
            cited = [evidence_by_id[evidence_id] for evidence_id in resolved_ids]
            details = _unsupported_details(claim, cited)
            if details:
                unsupported.append(claim)
                fabricated.extend(details)

        mismatches: list[str] = []
        for quote, claim in _quotes(draft.answer, claims):
            resolved_ids = _resolved(_tokens(claim), pack, evidence_by_id)
            pool = (
                [evidence_by_id[evidence_id] for evidence_id in resolved_ids]
                if resolved_ids
                else list(pack.evidence)
            )
            if not _quote_supported(quote, pool):
                mismatches.append(quote)

        citation_valid = not invalid and not unsupported and not mismatches
        final = self._confidence(
            draft, pack, invalid, unsupported, mismatches, fabricated
        )
        return ValidationResult(
            answer_status=draft.answer_status,
            citation_valid=citation_valid,
            unsupported_claims=unsupported,
            invalid_citations=invalid,
            quote_mismatches=mismatches,
            fabricated_details=list(dict.fromkeys(fabricated)),
            citations=valid_ids,
            final_confidence=final,
        )

    def _confidence(
        self,
        draft: AnswerDraft,
        pack: EvidencePack,
        invalid: list[str],
        unsupported: list[str],
        mismatches: list[str],
        fabricated: list[str],
    ) -> AnswerConfidence:
        if invalid or mismatches or fabricated:
            return AnswerConfidence.NONE
        if unsupported:
            return AnswerConfidence.LOW
        evidence_count = len(pack.evidence)
        kinds = {item.kind for item in pack.evidence}
        if evidence_count >= self.config.min_high_confidence_evidence and len(kinds) >= 2:
            computed = AnswerConfidence.HIGH
        elif evidence_count >= 1:
            computed = AnswerConfidence.MEDIUM
        else:
            computed = AnswerConfidence.LOW
        if draft.answer_status is AnswerStatus.CONTESTED:
            computed = _min_confidence(computed, AnswerConfidence.MEDIUM)
        return _min_confidence(draft.confidence, computed)


def _belongs_to_selection(
    item: EvidenceItem, selected_decisions: set[str]
) -> bool:
    return item.decision_id in selected_decisions


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    for group in _INLINE.findall(text):
        for part in re.split(r"[,;\s]+", group.strip()):
            cleaned = part.strip().strip("[]").strip()
            if cleaned:
                tokens.append(cleaned)
    return tokens


def _claims(answer: str) -> list[str]:
    claims: list[str] = []
    for line in answer.splitlines():
        stripped = _BULLET.sub("", line).strip()
        if not stripped:
            continue
        for sentence in _SENTENCE.split(stripped):
            cleaned = sentence.strip()
            if cleaned:
                claims.append(cleaned)
    return claims


def _resolved(
    tokens: list[str],
    pack: EvidencePack,
    evidence_by_id: dict[str, EvidenceItem],
) -> list[str]:
    resolved: list[str] = []
    for token in tokens:
        evidence_id = pack.resolve_citation(token)
        if evidence_id in evidence_by_id and evidence_id not in resolved:
            resolved.append(evidence_id)
    return resolved


def _is_factual(claim: str) -> bool:
    without_citations = _INLINE.sub(" ", claim)
    words = [word for word in re.split(r"\s+", without_citations) if word.strip()]
    return len(words) >= 3


def _details(claim: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(kind: str, value: str) -> None:
        key = (kind, value)
        if key not in seen:
            seen.add(key)
            found.append(key)

    for match in _FILE.finditer(claim):
        add("file", match.group(0))
    for match in _REFERENCE.finditer(claim):
        value = match.group(1) or match.group(2)
        if value:
            add("reference", value)
    for match in _ISO_DATE.finditer(claim):
        add("date", match.group(0))
    for match in _YEAR.finditer(claim):
        add("date", match.group(0))
    for match in _SHA.finditer(claim):
        add("sha", match.group(0))
    for match in _PERSON_BY.finditer(claim):
        add("person", match.group(1))
    for match in _PERSON_ACTION.finditer(claim):
        add("person", match.group(1))
    return found


def _unsupported_details(
    claim: str, evidence: Sequence[EvidenceItem]
) -> list[str]:
    unsupported: list[str] = []
    for kind, value in _details(claim):
        if not _detail_supported(kind, value, evidence):
            unsupported.append(_display_detail(kind, value))
    return unsupported


def _detail_supported(
    kind: str, value: str, evidence: Sequence[EvidenceItem]
) -> bool:
    needle = _normalize(value)
    if not needle:
        return True
    for item in evidence:
        if kind == "file":
            haystacks = [item.path or "", item.url or "", item.excerpt]
        elif kind == "reference":
            haystacks = [
                str(item.pr_number) if item.pr_number is not None else "",
                str(item.issue_number) if item.issue_number is not None else "",
                item.url or "",
                item.excerpt,
            ]
        elif kind == "date":
            haystacks = [item.created_at or "", item.excerpt]
        elif kind == "sha":
            haystacks = [item.commit_sha or "", item.url or "", item.excerpt]
        elif kind == "person":
            haystacks = [item.author or "", item.excerpt]
        else:  # pragma: no cover - defensive
            haystacks = [item.excerpt]
        if any(needle in _normalize(haystack) for haystack in haystacks):
            return True
    return False


def _display_detail(kind: str, value: str) -> str:
    if kind == "reference":
        return f"#{value}"
    return value


def _quotes(answer: str, claims: list[str]) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for match in _QUOTE.finditer(answer):
        quote = match.group(1) or match.group(2) or ""
        claim = next((item for item in claims if quote in item), "")
        result.append((quote, claim))
    return result


def _quote_supported(quote: str, evidence: Sequence[EvidenceItem]) -> bool:
    normalized = _normalize(quote)
    if not normalized:
        return True
    parts = [
        _normalize(part)
        for part in re.split(r"\.\.\.|…", quote)
        if len(_normalize(part)) >= 8
    ]

    if not parts:
        parts = [normalized]
    excerpts = [_normalize(item.excerpt) for item in evidence]
    return all(any(part in excerpt for excerpt in excerpts) for part in parts)


def _normalize(text: str) -> str:
    return " ".join(text.lower().split()).strip(" \t\n\r\"'")


def _min_confidence(
    left: AnswerConfidence, right: AnswerConfidence
) -> AnswerConfidence:
    return _BY_ORDER[min(_CONFIDENCE_ORDER[left], _CONFIDENCE_ORDER[right])]


__all__ = ["CitationValidator"]
