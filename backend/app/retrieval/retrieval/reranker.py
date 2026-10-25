"""Deterministic reranking on top of WRRF.

Fusion answers "which candidates did independent sources agree on?". This
stage answers the product-specific questions: is there a path from the asked
entity, is the evidence corroborated, is the decision still current, and is a
review comment anchored to the relevant region?

Every rule is a named config field and every applied rule writes a reason
string. The reranker never sees a raw cosine or BM25 score; it only adjusts the
fused rank score.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from app.retrieval.config import RetrievalConfig
from app.retrieval.models import (
    DecisionCandidate,
    DecisionState,
    EntityType,
    EvidenceItem,
    ExtractedEntity,
    FusedCandidate,
    GraphContext,
    QuestionUnderstanding,
    RankedCandidate,
    RerankSignals,
)

_SUPERSEDED_STATES = {
    DecisionState.SUPERSEDED,
    DecisionState.REVERSED,
}

_REGION_SUFFIX = re.compile(r"^(?P<path>.+):(?P<start>\d+)-(?P<end>\d+)$")


@dataclass(frozen=True)
class _EntityMatch:
    entity: ExtractedEntity
    target: str
    exact: bool


@dataclass(frozen=True)
class _Region:
    path: str
    start: int
    end: int


class Reranker:
    def __init__(
        self,
        config: RetrievalConfig | None = None,
        *,
        today: date | None = None,
    ) -> None:
        self.config = config or RetrievalConfig()
        # Injectable so ranking is reproducible in tests. Production computes
        # the date per request; no model or database clock is involved.
        self.today = today

    def rerank(
        self,
        *,
        fused: Sequence[FusedCandidate],
        contexts: Mapping[str, GraphContext],
        understanding: QuestionUnderstanding,
    ) -> list[RankedCandidate]:
        ranked: list[RankedCandidate] = []
        for item in fused:
            context = contexts.get(item.candidate.decision_id)
            signals = self.signals_for(
                item.candidate,
                context,
                understanding,
            )
            candidate = item.candidate
            if signals.superseded and candidate.status not in _SUPERSEDED_STATES:
                later = context.later_decisions[0] if context and context.later_decisions else None
                candidate = candidate.model_copy(
                    update={
                        "status": DecisionState.SUPERSEDED,
                        "superseded_by": later or candidate.superseded_by,
                    }
                )
            ranked.append(
                self.apply_signals(
                    item.model_copy(update={"candidate": candidate}), signals
                )
            )
        ranked.sort(key=lambda item: (-item.final_score, item.candidate.decision_id))
        return ranked[: self.config.max_decisions]

    def signals_for(
        self,
        candidate: DecisionCandidate,
        context: GraphContext | None,
        understanding: QuestionUnderstanding,
    ) -> RerankSignals:
        targets = _unique(
            [*candidate.target_ids, *(context.targets if context else [])]
        )
        matches = _matched_entities(understanding, targets)
        evidence = context.evidence if context else []
        kinds = {item.kind for item in evidence}
        superseded = candidate.status in _SUPERSEDED_STATES or bool(
            context and context.later_decisions
        )
        has_entities = bool(understanding.entities)
        target_related = True
        if has_entities and targets:
            target_related = bool(matches)
        # One evidence *kind* is one source. Five commits are still one weak
        # source; a commit plus a review comment are two independent ones.
        single_weak_evidence = len(kinds) <= 1
        stale = _is_stale_without_check(
            understanding=understanding,
            context=context,
            evidence=evidence,
            today=self.today or date.today(),
            max_age_days=self.config.stale_evidence_days,
        )
        return RerankSignals(
            graph_path_to_exact_entity=bool(
                context is not None and any(match.exact for match in matches)
            ),
            independent_evidence_sources=len(kinds),
            superseded=superseded,
            review_comment_on_region=_review_comment_on_region(
                evidence=evidence,
                targets=targets,
                understanding=understanding,
            ),
            target_matches_entity=bool(matches),
            target_related=target_related,
            stale_without_supersession_check=stale,
            single_weak_evidence=single_weak_evidence,
        )

    def apply_signals(
        self, fused: FusedCandidate, signals: RerankSignals
    ) -> RankedCandidate:
        cfg = self.config
        score = fused.score
        reasons: list[str] = []

        if signals.graph_path_to_exact_entity:
            score += cfg.graph_path_bonus
            reasons.append("direct graph path to the asked entity")
        if signals.independent_evidence_sources >= 2:
            score += cfg.multi_evidence_bonus
            reasons.append("corroborated by independent evidence")
        if signals.superseded:
            score -= cfg.superseded_penalty
            reasons.append("superseded or reversed: historical only")
        else:
            score += cfg.active_bonus
            reasons.append("no supersession edge in the ingested window")
        if signals.review_comment_on_region:
            score += cfg.review_anchor_bonus
            reasons.append("review comment anchored to the relevant region")
        if signals.target_matches_entity:
            score += cfg.target_match_bonus
            reasons.append("target matches the extracted entity")
        if signals.single_weak_evidence and signals.independent_evidence_sources < 2:
            score -= cfg.single_weak_evidence_penalty
            reasons.append("only one weak evidence source")
        if not signals.target_related:
            score -= cfg.unrelated_target_penalty
            reasons.append("target is unrelated to the question")
        if signals.stale_without_supersession_check:
            score -= cfg.stale_without_check_penalty
            reasons.append("stale evidence without a supersession check")

        return RankedCandidate(
            candidate=fused.candidate,
            fused_score=fused.score,
            final_score=round(score, 12),
            signals=signals,
            reasons=reasons,
        )


def _matched_entities(
    understanding: QuestionUnderstanding, targets: Sequence[str]
) -> list[_EntityMatch]:
    matches: list[_EntityMatch] = []
    for entity in understanding.entities:
        identities = _identities(entity)
        if not identities:
            continue
        for target in targets:
            label, tail = _split_target(target)
            if not tail:
                continue
            exact = _exact_target_match(entity, label, tail, identities)
            if exact:
                matches.append(_EntityMatch(entity, target, True))
                break
            if _loose_target_match(label, tail, identities):
                matches.append(_EntityMatch(entity, target, False))
                break
    return matches


def _identities(entity: ExtractedEntity) -> list[str]:
    values = [entity.normalized]
    if entity.graph_key:
        values.append(entity.graph_key)
    return _unique(value.strip() for value in values if value and value.strip())


def _split_target(target: str) -> tuple[str, str]:
    label, separator, tail = target.strip().partition(":")
    if not separator:
        return "", label
    return label, tail.strip()


def _exact_target_match(
    entity: ExtractedEntity,
    label: str,
    tail: str,
    identities: Sequence[str],
) -> bool:
    if any(_same_identifier(identity, tail) for identity in identities):
        return True
    # A file question can name the file that contains a governed region even
    # when the graph stores only the CodeRegion target.
    region = _parse_region(tail) if label == "CodeRegion" else None
    return bool(
        region
        and entity.type is EntityType.FILE
        and _same_path(_identifier_tail(entity.normalized), region.path)
    )


def _loose_target_match(
    label: str, tail: str, identities: Sequence[str]
) -> bool:
    normalized_tail = _normalize(tail)
    if not normalized_tail:
        return False
    for identity in identities:
        wanted = _normalize(_identifier_tail(identity))
        if not wanted:
            continue
        if wanted == normalized_tail:
            return True
        if len(wanted) >= 3 and wanted in normalized_tail:
            return True
        if len(normalized_tail) >= 3 and normalized_tail in wanted:
            return True
    region = _parse_region(tail) if label == "CodeRegion" else None
    if region is not None:
        return any(
            _same_path(entity_path, region.path)
            for identity in identities
            if (entity_path := _identifier_tail(identity))
        )
    return False


def _same_identifier(value: str, target: str) -> bool:
    return _normalize(_identifier_tail(value)) == _normalize(
        _identifier_tail(target)
    )


def _identifier_tail(value: str) -> str:
    """Drop a ``Label:`` prefix from a graph key, leaving the identifier."""
    _, separator, tail = value.strip().partition(":")
    return tail.strip() if separator else value.strip()


def _parse_region(value: str) -> _Region | None:
    match = _REGION_SUFFIX.match(value.strip())
    if match is None:
        return None
    start = int(match.group("start"))
    end = int(match.group("end"))
    if end < start:
        return None
    return _Region(path=match.group("path"), start=start, end=end)


def _review_comment_on_region(
    *,
    evidence: Sequence[EvidenceItem],
    targets: Sequence[str],
    understanding: QuestionUnderstanding,
) -> bool:
    regions, files = _relevant_paths(targets, understanding)
    if not regions and not files:
        return False
    for item in evidence:
        if item.kind != "review_comment" or not item.path:
            continue
        if item.start_line is None:
            continue
        matching_regions = [
            region for region in regions if _same_path(item.path or "", region.path)
        ]
        if matching_regions:
            if any(_overlaps(item, region) for region in matching_regions):
                return True
            # The comment is in the right file but outside the governed region;
            # do not treat it as an anchor for this decision.
            continue
        if any(_same_path(item.path, path) for path in files):
            return True
    return False


def _relevant_paths(
    targets: Sequence[str], understanding: QuestionUnderstanding
) -> tuple[list[_Region], set[str]]:
    regions: list[_Region] = []
    files: set[str] = set()
    for target in targets:
        label, tail = _split_target(target)
        if label == "CodeRegion":
            region = _parse_region(tail)
            if region is not None:
                regions.append(region)
        elif label == "File" and tail:
            files.add(tail)
    for entity in understanding.entities:
        if entity.type is EntityType.FILE:
            files.add(entity.normalized)
    return regions, files


def _overlaps(item: EvidenceItem, region: _Region) -> bool:
    start = item.start_line
    if start is None:
        return False
    end = item.end_line if item.end_line is not None else start
    return start <= region.end and end >= region.start


def _is_stale_without_check(
    *,
    understanding: QuestionUnderstanding,
    context: GraphContext | None,
    evidence: Sequence[EvidenceItem],
    today: date,
    max_age_days: int,
) -> bool:
    if understanding.requires_supersession_check:
        return False
    if context is not None and context.later_decisions:
        return False
    dates = [
        parsed
        for item in evidence
        if (parsed := _as_date(item.created_at)) is not None
    ]
    if not dates:
        # Age is unknown, so the candidate cannot be called old. It can still
        # be weak or unrelated on the other signals.
        return False
    return (today - max(dates)).days > max_age_days


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.date()


def _normalize(value: str) -> str:
    return " ".join(value.split()).casefold()


def _same_path(left: str, right: str) -> bool:
    left_path = _normalize_path(left)
    right_path = _normalize_path(right)
    if not left_path or not right_path:
        return False
    return (
        left_path == right_path
        or left_path.endswith("/" + right_path)
        or right_path.endswith("/" + left_path)
    )


def _normalize_path(value: str) -> str:
    path = value.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path.strip("/").casefold()


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


__all__ = ["Reranker"]
