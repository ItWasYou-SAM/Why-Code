"""Question-to-entity extraction.

The deterministic pass runs first. Exact identifiers always win. Lexical
matches must survive a distinctiveness check so a generic word such as
``client`` cannot select a symbol on its own. Only when the result is empty or
ambiguous does the constrained LLM step run, and it may only choose
``option_id`` values from the shortlist supplied in the prompt.

The LLM never supplies a graph key, a normalised identifier or a question
class. Those are copied from the catalog or computed by the classifier.
"""

from __future__ import annotations

import calendar
import math
import re
from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.core.errors import InvalidRecordError
from app.intent.llm.provider import OpenAICompatibleProvider
from app.intent.llm.structured_client import StructuredClient
from app.retrieval.config import RetrievalConfig
from app.retrieval.models import QuestionType
from app.retrieval.question.entity_catalog import EntityCatalog
from app.retrieval.question.models import (
    ENTITY_TYPE_ORDER,
    CatalogEntity,
    Classification,
    EntityType,
    ExtractedEntity,
    ExtractionMethod,
    LLMEntityChoice,
    LLMEntitySelection,
    MatchSource,
    QuestionUnderstanding,
    TimeConstraint,
    TimeConstraintKind,
)
from app.retrieval.question.normalization import (
    clean_question,
    content_tokens,
    split_identifier,
    token_forms,
)
from app.retrieval.question.question_classifier import QuestionClassifier

_EXACT_BOUNDARY = r"(?<![A-Za-z0-9_]){surface}(?![A-Za-z0-9_])"
_STRUCTURAL_MARKS = frozenset("._/@:")
_BETWEEN_YEARS = re.compile(
    r"\bbetween\s+((?:19|20)\d{2})(?:-(\d{2}))?\s+"
    r"and\s+((?:19|20)\d{2})(?:-(\d{2}))?\b",
    re.IGNORECASE,
)
_AFTER_YEARS = re.compile(
    r"\b(?:since|after)\s+((?:19|20)\d{2})(?:-(\d{2}))?\b",
    re.IGNORECASE,
)
_BEFORE_YEARS = re.compile(
    r"\b(?:before|prior\s+to)\s+((?:19|20)\d{2})(?:-(\d{2}))?\b",
    re.IGNORECASE,
)
_DURING_YEARS = re.compile(
    r"\bin\s+((?:19|20)\d{2})(?:-(\d{2}))?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Profile:
    entity: CatalogEntity
    exact_surfaces: tuple[str, ...]
    identifier_buckets: tuple[frozenset[str], ...]
    claim_buckets: tuple[frozenset[str], ...]
    all_buckets: tuple[frozenset[str], ...]


@dataclass(frozen=True)
class _Scored:
    entity: CatalogEntity
    text: str
    source: MatchSource
    score: float
    matched_tokens: tuple[str, ...] = ()


@dataclass(frozen=True)
class KnownEntity:
    """The compact entity form used by the pipeline and its fixtures.

    The Neo4j catalog carries the same information plus a graph key. Keeping
    this small form makes service tests dependency-free while the extractor
    still resolves every ``KnownEntity`` to the same internal profile model.
    """

    type: EntityType
    identifier: str
    aliases: tuple[str, ...] = ()
    graph_key: str | None = None
    display_name: str | None = None
    path: str | None = None
    claim: str | None = None
    status: str | None = None
    pr_number: int | None = None
    source_label: str | None = None


class EntityIndex:
    """An immutable allowlist of entities the extractor may return."""

    def __init__(self, entities: Sequence[KnownEntity] = ()) -> None:
        self.entities = tuple(entities)

    def __iter__(self) -> Iterator[KnownEntity]:
        return iter(self.entities)

    def __len__(self) -> int:
        return len(self.entities)

    def catalog_entries(self) -> list[CatalogEntity]:
        return [_known_entity_to_catalog(entity) for entity in self.entities]



class EntityExtractor:
    def __init__(
        self,
        catalog: EntityCatalog | None = None,
        config: RetrievalConfig | None = None,
        *,
        index: EntityIndex | None = None,
        classifier: QuestionClassifier | None = None,
        structured_client: StructuredClient | None = None,
    ) -> None:
        if catalog is None and index is None:
            raise InvalidRecordError(
                "entity extraction needs a catalog or an index",
                component="retrieval",
            )
        catalog_repo = getattr(catalog, "repo", None) if catalog else None
        config_repo = config.repo if config is not None else None
        if catalog_repo and config_repo and catalog_repo != config_repo:
            raise InvalidRecordError(
                "catalog repo does not match retrieval config",
                component="retrieval",
            )
        self.repo = config_repo or catalog_repo
        self.config = config or RetrievalConfig(repo=self.repo)
        self.catalog = catalog
        self.index = index
        self.classifier = classifier or QuestionClassifier()
        if catalog is not None:
            entries = list(catalog.entries())
        else:
            assert index is not None
            entries = index.catalog_entries()
        self._profiles = _profiles(entries)
        if not self.config.enable_llm_entity_extraction:
            self._client = None
        elif structured_client is not None:
            self._client = structured_client
        elif self.config.llm_available:
            self._client = _build_structured_client(self.config)
        else:
            self._client = None

    def extract(
        self,
        question: str,
        question_type: QuestionType | None = None,
    ) -> QuestionUnderstanding:
        text = clean_question(
            question, max_chars=self.config.max_question_chars
        )
        tokens = content_tokens(text)
        candidates = self._candidates(text, tokens)
        accepted = [
            candidate
            for candidate in candidates
            if candidate.source is MatchSource.EXACT
            or candidate.score >= self.config.lexical_accept_score
        ]
        ambiguous = _has_ambiguity(accepted, self.config.lexical_accept_margin)
        needs_llm = (not accepted and bool(candidates)) or ambiguous

        warnings: list[str] = []
        if not self._profiles:
            warnings.append("empty_catalog")

        provisional_types = {item.entity.type for item in accepted}
        if question_type is None:
            provisional = self.classifier.classify_details(
                text, entity_types=provisional_types
            )
        else:
            provisional = Classification(
                question_type=question_type,
                scores={},
                reasons=["caller_supplied_question_type"],
                requires_supersession_check=question_type
                in {
                    QuestionType.CONFIG_WHY,
                    QuestionType.CODE_WHY,
                    QuestionType.DECISION_HISTORY,
                    QuestionType.SUPERSESSION,
                },
            )

        method = ExtractionMethod.DETERMINISTIC
        final = accepted
        if needs_llm and self._profiles:
            shortlist = candidates[: self.config.max_catalog_options]
            if self._client is None:
                final = _fallback(candidates, self.config.fallback_score)
                method = ExtractionMethod.DETERMINISTIC_FALLBACK
                warnings.append("llm_unavailable")
            else:
                selected, llm_warnings, llm_ok = self._select_with_llm(
                    text, provisional, shortlist
                )
                warnings.extend(llm_warnings)
                if llm_ok:
                    final = _merge(accepted + selected)
                    if selected and accepted:
                        method = ExtractionMethod.HYBRID
                    elif selected:
                        method = ExtractionMethod.LLM
                    else:
                        method = ExtractionMethod.DETERMINISTIC
                else:
                    final = _fallback(candidates, self.config.fallback_score)
                    method = ExtractionMethod.DETERMINISTIC_FALLBACK
                if ambiguous:
                    warnings.append("ambiguous_entities")
        elif ambiguous:
            warnings.append("ambiguous_entities")

        if not final and self._profiles:
            warnings.append("no_entity_match")

        final = _merge(final)[: self.config.max_entities]
        final_types = {item.entity.type for item in final}
        classification = self.classifier.classify_details(
            text, entity_types=final_types
        )
        return QuestionUnderstanding(
            question=text,
            question_type=classification.question_type,
            repo=self.repo,
            classification=classification,
            entities=[_public_entity(item) for item in final],
            time_constraint=_parse_time_constraint(text),
            requires_supersession_check=classification.requires_supersession_check,
            extraction_method=method,
            warnings=_dedupe(warnings),
        )

    def _candidates(self, question: str, tokens: Sequence[str]) -> list[_Scored]:
        if not tokens or not self._profiles:
            return []
        distinct = _distinctive_tokens(tokens, self._profiles)
        scored: list[_Scored] = []
        for profile in self._profiles:
            exact = _exact_candidate(profile, question)
            if exact is not None:
                scored.append(exact)
                continue
            lexical = _lexical_candidate(
                profile,
                question,
                tokens,
                distinct,
                self.config.min_candidate_score,
            )
            if lexical is not None:
                scored.append(lexical)
        return sorted(
            scored,
            key=lambda item: (
                -item.score,
                ENTITY_TYPE_ORDER[item.entity.type],
                item.entity.identifier.casefold(),
                item.entity.graph_key,
            ),
        )

    def _select_with_llm(
        self,
        question: str,
        classification: Classification,
        shortlist: Sequence[_Scored],
    ) -> tuple[list[_Scored], list[str], bool]:
        if not shortlist:
            return [], [], True
        options = {
            f"O{index}": candidate
            for index, candidate in enumerate(shortlist, start=1)
        }
        system = (
            "You map a user question to entities that already exist in a "
            "codebase graph. Select only options that are directly relevant "
            "to the question. Never invent an identifier, path, person or "
            "decision. Never answer the question. If no option is relevant, "
            "return an empty entities list and set no_match_reason."
        )
        user = _render_options(question, classification, shortlist)
        assert self._client is not None
        result = self._client.extract(
            system=system,
            user=user,
            schema_model=LLMEntitySelection,
            schema_name="retrieval_entities",
        )
        if not result.ok:
            detail = result.error_text[:160]
            return [], [f"llm_failed:{detail}"], False
        try:
            selection = LLMEntitySelection.model_validate(result.data or {})
        except ValueError:
            return [], ["llm_invalid_selection"], False

        selected: list[_Scored] = []
        warnings: list[str] = []
        used: set[str] = set()
        invalid_option = False
        for choice in selection.entities:
            option = _resolve_choice(choice, options, shortlist)
            if option is None:
                marker = choice.option_id or choice.normalized or "unknown"
                warnings.append(f"llm_unknown_option:{marker}")
                invalid_option = True
                continue
            if choice.type is not None and choice.type is not option.entity.type:
                warnings.append(
                    f"llm_type_mismatch:{choice.option_id or choice.normalized}"
                )
                invalid_option = True
                continue
            identity = choice.option_id or option.entity.graph_key
            if identity in used:
                continue
            used.add(identity)
            selected.append(
                _Scored(
                    entity=option.entity,
                    text=_safe_llm_text(choice.text, option.entity.identifier),
                    source=MatchSource.LLM,
                    score=0.9,
                )
            )
        if not selected and selection.no_match_reason:
            warnings.append("llm_no_match")
        if not selected and invalid_option:
            return [], warnings, False
        return selected, warnings, True


class QuestionAnalyzer:
    """The service-facing wrapper around deterministic understanding.

    ``llm`` is optional. It is only consulted by the extractor when the
    deterministic pass has a non-empty shortlist but no confident selection;
    a question with no catalog candidates never reaches the model.
    """

    def __init__(
        self,
        *,
        index: EntityIndex | None = None,
        llm: Any | None = None,
    ) -> None:
        self.index = index or EntityIndex()
        self.llm = llm

    def analyze(self, question: str) -> QuestionUnderstanding:
        client = (
            StructuredClient(self.llm, max_attempts=1)
            if self.llm is not None
            else None
        )
        return EntityExtractor(
            index=self.index,
            config=RetrievalConfig(),
            structured_client=client,
        ).extract(question)


def _resolve_choice(
    choice: LLMEntityChoice,
    options: Mapping[str, _Scored],
    shortlist: Sequence[_Scored],
) -> _Scored | None:
    if choice.option_id is not None:
        return options.get(choice.option_id)
    if choice.normalized:
        wanted = choice.normalized.strip().casefold()
        return next(
            (
                candidate
                for candidate in shortlist
                if wanted
                in {
                    candidate.entity.identifier.casefold(),
                    *(alias.casefold() for alias in candidate.entity.aliases),
                }
            ),
            None,
        )
    return None


def _known_entity_to_catalog(entity: KnownEntity) -> CatalogEntity:
    return CatalogEntity(
        graph_key=entity.graph_key or entity.identifier,
        type=entity.type,
        identifier=entity.identifier,
        display_name=entity.display_name,
        path=entity.path,
        claim=entity.claim,
        status=entity.status,
        pr_number=entity.pr_number,
        source_label=entity.source_label,
        aliases=tuple(entity.aliases),
    )


def _profiles(entities: Sequence[CatalogEntity]) -> list[_Profile]:
    profiles: list[_Profile] = []
    for entity in entities:
        token_sources: list[str | None] = []
        if (
            entity.type is EntityType.SYMBOL
            and "." in entity.identifier
        ):
            # A qualified name's module prefix is useful for exact matching
            # but too generic for lexical coverage; compare the last two
            # components (``Client.send``) plus explicit aliases.
            token_sources.append(".".join(entity.identifier.split(".")[-2:]))
        else:
            token_sources.append(entity.identifier)
        token_sources.extend((entity.display_name, *entity.aliases))
        identifier_tokens = _unique_tokens(
            token
            for value in token_sources
            if value
            for token in split_identifier(value)
        )
        claim_tokens = (
            _unique_tokens(split_identifier(entity.claim))
            if entity.claim
            else []
        )
        identifier_buckets = tuple(
            token_forms(token) for token in identifier_tokens
        )
        claim_buckets = tuple(token_forms(token) for token in claim_tokens)
        all_buckets = identifier_buckets + claim_buckets
        surfaces = _unique_surfaces(
            entity.identifier,
            *(entity.aliases if entity.type is not EntityType.DECISION else ()),
        )
        profiles.append(
            _Profile(
                entity=entity,
                exact_surfaces=surfaces,
                identifier_buckets=identifier_buckets,
                claim_buckets=claim_buckets,
                all_buckets=all_buckets,
            )
        )
    return profiles


def _unique_tokens(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    tokens: list[str] = []
    for token in values:
        if len(token) < 2 or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _unique_surfaces(*values: str | None) -> tuple[str, ...]:
    seen: set[str] = set()
    surfaces: list[str] = []
    for value in values:
        if value is None:
            continue
        candidate = value.strip()
        folded = candidate.casefold()
        if not candidate or folded in seen:
            continue
        seen.add(folded)
        surfaces.append(candidate)
    return tuple(surfaces)


def _exact_candidate(profile: _Profile, question: str) -> _Scored | None:
    for index, surface in enumerate(profile.exact_surfaces):
        is_identifier = index == 0
        if not _exact_surface_allowed(
            profile.entity.type, surface, is_identifier
        ):
            continue
        pattern = re.compile(
            _EXACT_BOUNDARY.format(surface=re.escape(surface)),
            re.IGNORECASE,
        )
        match = pattern.search(question)
        if match is None:
            continue
        return _Scored(
            entity=profile.entity,
            text=_safe_llm_text(match.group(0), profile.entity.identifier),
            source=MatchSource.EXACT,
            score=1.0,
        )
    return None


def _exact_surface_allowed(
    entity_type: EntityType,
    surface: str,
    is_identifier: bool,
) -> bool:
    if is_identifier:
        return not (
            entity_type is EntityType.SYMBOL
            and not _structural(surface)
            and len(surface) < 6
        )
    if entity_type is EntityType.DECISION:
        return False
    if len(surface) < 6 and not _structural(surface):
        return False
    return not (entity_type is EntityType.PERSON and len(surface) < 3)


def _structural(value: str) -> bool:
    return any(mark in value for mark in _STRUCTURAL_MARKS)


def _lexical_candidate(
    profile: _Profile,
    question: str,
    tokens: Sequence[str],
    distinctive: Collection[str],
    minimum: float,
) -> _Scored | None:
    if profile.entity.type is EntityType.DECISION:
        return _decision_candidate(profile, tokens, distinctive)
    matched = [
        token
        for token in tokens
        if _matches_buckets(profile.identifier_buckets, token)
    ]
    if not matched:
        return None
    if len(matched) == 1 and matched[0] not in distinctive:
        return None
    if not profile.identifier_buckets:
        return None
    matched_forms = frozenset(
        form for token in matched for form in token_forms(token)
    )
    matched_buckets = sum(
        1
        for bucket in profile.identifier_buckets
        if bucket & matched_forms
    )
    identifier_coverage = matched_buckets / len(profile.identifier_buckets)
    query_coverage = len(matched) / len(tokens)
    score = round(0.7 * identifier_coverage + 0.3 * query_coverage, 3)
    if score < minimum:
        return None
    return _Scored(
        entity=profile.entity,
        text=" ".join(_ordered_matches(tokens, matched)),
        source=MatchSource.LEXICAL,
        score=min(score, 0.99),
        matched_tokens=tuple(matched),
    )


def _decision_candidate(
    profile: _Profile,
    tokens: Sequence[str],
    distinctive: Collection[str],
) -> _Scored | None:
    if not profile.claim_buckets:
        return None
    matched = [
        token
        for token in tokens
        if _matches_buckets(profile.claim_buckets, token)
    ]
    if len(matched) < 2:
        return None
    if not any(token in distinctive for token in matched):
        return None
    score = round(min(0.9, 0.55 + 0.1 * len(matched)), 3)
    return _Scored(
        entity=profile.entity,
        text=" ".join(_ordered_matches(tokens, matched)),
        source=MatchSource.LEXICAL,
        score=score,
        matched_tokens=tuple(matched),
    )


def _matches_buckets(
    buckets: Sequence[frozenset[str]], token: str
) -> bool:
    forms = token_forms(token)
    return any(bucket & forms for bucket in buckets)


def _ordered_matches(
    tokens: Sequence[str], matched: Collection[str]
) -> list[str]:
    matched_set = set(matched)
    return [token for token in tokens if token in matched_set]


def _distinctive_tokens(
    tokens: Sequence[str], profiles: Sequence[_Profile]
) -> frozenset[str]:
    if not profiles:
        return frozenset()
    limit = max(2, math.ceil(len(profiles) * 0.25))
    distinctive: set[str] = set()
    for token in tokens:
        forms = token_forms(token)
        document_frequency = sum(
            1
            for profile in profiles
            if any(bucket & forms for bucket in profile.all_buckets)
        )
        if document_frequency <= limit:
            distinctive.add(token)
    return frozenset(distinctive)


def _has_ambiguity(
    accepted: Sequence[_Scored], margin: float
) -> bool:
    if any(candidate.source is MatchSource.EXACT for candidate in accepted):
        # An exact catalog identifier is already a confident anchor. Nearby
        # decision-claim scores may add context, but they are not grounds to
        # hand the question to the model.
        return False
    by_type: dict[EntityType, list[float]] = {}
    for candidate in accepted:
        by_type.setdefault(candidate.entity.type, []).append(candidate.score)
    for scores in by_type.values():
        if len(scores) < 2:
            continue
        ordered = sorted(scores, reverse=True)
        if round(ordered[0] - ordered[1], 9) < margin:
            return True
    return False


def _fallback(
    candidates: Sequence[_Scored], minimum: float
) -> list[_Scored]:
    return [
        candidate
        for candidate in candidates
        if candidate.score >= minimum
    ]


def _merge(candidates: Sequence[_Scored]) -> list[_Scored]:
    best: dict[str, _Scored] = {}
    for candidate in candidates:
        current = best.get(candidate.entity.graph_key)
        if current is None or _preference(candidate) > _preference(current):
            best[candidate.entity.graph_key] = candidate
    return sorted(
        best.values(),
        key=lambda item: (
            ENTITY_TYPE_ORDER[item.entity.type],
            -item.score,
            item.entity.identifier.casefold(),
            item.entity.graph_key,
        ),
    )


def _preference(candidate: _Scored) -> tuple[int, float]:
    source_rank = {
        MatchSource.EXACT: 3,
        MatchSource.LEXICAL: 2,
        MatchSource.LLM: 1,
    }
    return source_rank[candidate.source], candidate.score


def _public_entity(candidate: _Scored) -> ExtractedEntity:
    return ExtractedEntity(
        type=candidate.entity.type,
        text=candidate.text,
        normalized=candidate.entity.identifier,
        graph_key=candidate.entity.graph_key,
        match=candidate.source,
        score=candidate.score,
    )


def _render_options(
    question: str,
    classification: Classification,
    shortlist: Sequence[_Scored],
) -> str:
    lines = [
        f"Question: {question}",
        f"Question type: {classification.question_type.value}",
        "",
        "Options:",
    ]
    for index, candidate in enumerate(shortlist, start=1):
        entity = candidate.entity
        parts = [
            f"O{index}",
            f"type={entity.type.value}",
            f"identifier={entity.identifier}",
            f"key={entity.graph_key}",
        ]
        if entity.path:
            parts.append(f"path={entity.path}")
        if entity.status:
            parts.append(f"status={entity.status}")
        if entity.claim:
            parts.append(f"claim={entity.claim[:300]}")
        lines.append(" | ".join(parts))
    lines.extend(
        [
            "",
            "Return JSON with only option_id values from the list above.",
            f"Select at most {min(len(shortlist), 8)} options.",
        ]
    )
    return "\n".join(lines)


def _safe_llm_text(value: str, fallback: str) -> str:
    text = " ".join(value.replace("\x00", " ").split())
    if not text:
        text = fallback
    return text[:200]


def _build_structured_client(config: RetrievalConfig) -> StructuredClient:
    provider = OpenAICompatibleProvider(
        api_key=config.llm_api_key,
        model=config.llm_model,
        base_url=config.llm_base_url,
        timeout_seconds=config.llm_timeout_seconds,
        temperature=config.llm_temperature,
        seed=config.llm_seed,
        max_output_tokens=config.llm_max_output_tokens,
    )
    return StructuredClient(
        provider,
        max_attempts=config.llm_max_attempts,
        retry_base_delay=config.llm_retry_base_delay,
    )


def _parse_time_constraint(text: str) -> TimeConstraint | None:
    match = _BETWEEN_YEARS.search(text)
    if match is not None:
        start = _period_start(int(match.group(1)), _month(match.group(2)))
        end = _period_end(int(match.group(3)), _month(match.group(4)))
        return TimeConstraint(
            kind=TimeConstraintKind.BETWEEN,
            start=start.isoformat(),
            end=end.isoformat(),
            raw=match.group(0),
        )
    match = _AFTER_YEARS.search(text)
    if match is not None:
        period_start = _period_start(
            int(match.group(1)), _month(match.group(2))
        )
        start = period_start
        if match.group(0).lower().startswith("after"):
            start = _next_period_start(
                int(match.group(1)), _month(match.group(2))
            )
        return TimeConstraint(
            kind=TimeConstraintKind.AFTER,
            start=start.isoformat(),
            end=None,
            raw=match.group(0),
        )
    match = _BEFORE_YEARS.search(text)
    if match is not None:
        period_start = _period_start(
            int(match.group(1)), _month(match.group(2))
        )
        return TimeConstraint(
            kind=TimeConstraintKind.BEFORE,
            start=None,
            end=(period_start - timedelta(days=1)).isoformat(),
            raw=match.group(0),
        )
    match = _DURING_YEARS.search(text)
    if match is not None:
        start = _period_start(int(match.group(1)), _month(match.group(2)))
        end = _period_end(int(match.group(1)), _month(match.group(2)))
        return TimeConstraint(
            kind=TimeConstraintKind.DURING,
            start=start.isoformat(),
            end=end.isoformat(),
            raw=match.group(0),
        )
    return None


def _month(value: str | None) -> int | None:
    if value is None:
        return None
    month = int(value)
    return month if 1 <= month <= 12 else None


def _period_start(year: int, month: int | None) -> date:
    return date(year, month or 1, 1)


def _period_end(year: int, month: int | None) -> date:
    if month is None:
        return date(year, 12, 31)
    return date(year, month, calendar.monthrange(year, month)[1])


def _next_period_start(year: int, month: int | None) -> date:
    if month is None:
        return date(year + 1, 1, 1)
    if month == 12:
        return date(year + 1, 1, 1)
    return date(year, month + 1, 1)


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


__all__ = [
    "EntityExtractor",
    "EntityIndex",
    "KnownEntity",
    "QuestionAnalyzer",
]
