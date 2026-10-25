"""Intent extraction pipeline.

This is a pipeline, not an agent. Each PR is detected, packed, sent through
one bounded structured call per candidate class, then evidence-validated,
target-resolved, normalised, de-duplicated and order-checked. The LLM never
touches SQLite or Neo4j.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import AppError, InvalidRecordError
from app.intent.config import IntentConfig
from app.intent.context.context_packer import ContextPacker, EvidencePack
from app.intent.extraction.decision_extractor import DecisionExtractor
from app.intent.extraction.incident_extractor import IncidentExtractor
from app.intent.extraction.participant_extractor import ParticipantExtractor
from app.intent.graph.intent_records import IntentGraph, build_intent_graph
from app.intent.llm.provider import LLMProvider
from app.intent.llm.schemas import ExtractedCandidate
from app.intent.llm.structured_client import StructuredClient
from app.intent.models import (
    ConfidenceResult,
    DecisionStatus,
    EvidenceRef,
    IncidentIntent,
    IntentBase,
    IntentSummary,
    Participant,
    ReviewReason,
)
from app.intent.normalization import IntentNormalizer, build_evidence_ref
from app.intent.review.review_queue import ReviewQueue
from app.intent.selection.candidate_detector import (
    Candidate,
    CandidateClass,
    CandidateDetector,
)
from app.intent.selection.evidence_selector import EvidenceItem, PrEvidenceBundle
from app.intent.validation.deduplicator import (
    ConflictEdge,
    Deduplicator,
    DuplicateDecision,
    DuplicateMerge,
)
from app.intent.validation.deduplicator_judge import DuplicateJudge
from app.intent.validation.evidence_validator import (
    CandidateValidation,
    EvidenceLookup,
    EvidenceValidationResult,
    EvidenceValidator,
    FinalStatus,
)
from app.intent.validation.extraction_rules import ExtractionRuleValidator
from app.intent.validation.graph_link_validator import (
    GraphIndex,
    GraphLinkValidator,
    TargetValidation,
)
from app.intent.validation.supersession import (
    StatusUpdate,
    SupersessionDecision,
    SupersessionDetector,
    SupersessionEdge,
)
from app.intent.validation.supersession_judge import SupersessionJudge

_SOURCE_CANDIDATE_BY_DECISION_TYPE = {
    "constraint": CandidateClass.CONSTRAINT_CANDIDATE.value,
    "reversal": CandidateClass.REVERSAL_CANDIDATE.value,
}
_CANDIDATE_PRIORITY = {
    CandidateClass.DECISION_CANDIDATE: 3,
    CandidateClass.CONSTRAINT_CANDIDATE: 2,
    CandidateClass.REVERSAL_CANDIDATE: 1,
}


@dataclass
class IntentRunResult:
    repo: str
    intents: list[IntentBase] = field(default_factory=list)
    incidents: list[IncidentIntent] = field(default_factory=list)
    participants: list[Participant] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    graph: IntentGraph | None = None
    review_queue: ReviewQueue | None = None
    summary: IntentSummary = field(default_factory=IntentSummary)
    supersession_edges: list[SupersessionEdge] = field(default_factory=list)
    supersession_decisions: list[SupersessionDecision] = field(
        default_factory=list
    )
    duplicate_merges: list[DuplicateMerge] = field(default_factory=list)
    duplicate_decisions: list[DuplicateDecision] = field(default_factory=list)
    conflict_edges: list[ConflictEdge] = field(default_factory=list)
    status_updates: list[StatusUpdate] = field(default_factory=list)
    evidence_validations: list[EvidenceValidationResult] = field(
        default_factory=list
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "summary": self.summary.as_dict(),
            "intents": [intent.model_dump(mode="json") for intent in self.intents],
            "incidents": [incident.model_dump(mode="json") for incident in self.incidents],
            "participants": [item.model_dump(mode="json") for item in self.participants],
            "evidence_validations": [
                item.model_dump(mode="json") for item in self.evidence_validations
            ],
            "supersession_edges": [
                {
                    "rel_type": edge.rel_type,
                    "start_id": edge.start_id,
                    "end_id": edge.end_id,
                    "reason": edge.reason,
                }
                for edge in self.supersession_edges
            ],
            "supersession_decisions": [
                {
                    "earlier_decision_id": decision.earlier_decision_id,
                    "later_decision_id": decision.later_decision_id,
                    "relationship": decision.relationship,
                    "evidence_ids": list(decision.evidence_ids),
                    "explanation": decision.explanation,
                    "target_keys": list(decision.target_keys),
                }
                for decision in self.supersession_decisions
            ],
            "duplicate_merges": [
                {
                    "earlier_id": merge.earlier_id,
                    "later_id": merge.later_id,
                    "similarity": merge.similarity,
                    "exact": merge.exact,
                    "reason": merge.reason,
                    "evidence_ids": list(merge.evidence_ids),
                    "target_keys": list(merge.target_keys),
                }
                for merge in self.duplicate_merges
            ],
            "duplicate_decisions": [
                {
                    "earlier_id": decision.earlier_id,
                    "later_id": decision.later_id,
                    "relationship": decision.relationship,
                    "similarity": decision.similarity,
                    "exact": decision.exact,
                    "evidence_ids": list(decision.evidence_ids),
                    "explanation": decision.explanation,
                    "target_keys": list(decision.target_keys),
                    "high_impact": decision.high_impact,
                    "applied": decision.applied,
                }
                for decision in self.duplicate_decisions
            ],
            "conflict_edges": [
                {
                    "rel_type": edge.rel_type,
                    "start_id": edge.start_id,
                    "end_id": edge.end_id,
                    "reason": edge.reason,
                }
                for edge in self.conflict_edges
            ],
            "graph_counts": (self.graph.counts() if self.graph else {}),
        }


class IntentPipeline:
    def __init__(
        self,
        *,
        config: IntentConfig,
        store: EvidenceLookup,
        index: GraphIndex,
        provider: LLMProvider | None = None,
        structured_client: StructuredClient | None = None,
        logger: Any | None = None,
        clock: Any | None = None,
        detector: CandidateDetector | None = None,
        deduplicator: Deduplicator | None = None,
        duplicate_judge: DuplicateJudge | None = None,
        supersession_judge: SupersessionJudge | None = None,
    ) -> None:
        if provider is None and structured_client is None:
            raise InvalidRecordError(
                "IntentPipeline needs an LLM provider", component="intent"
            )
        self.config = config
        self.store = store
        self.index = index
        self.client = structured_client or StructuredClient(
            provider,  # type: ignore[arg-type]
            max_attempts=config.max_attempts,
            retry_base_delay=config.retry_base_delay,
            on_retry=_log_retry(logger),
        )
        self.logger = logger
        self.clock = clock
        self.detector = detector or CandidateDetector(
            decision_threshold=config.decision_score_threshold
        )
        self.packer = ContextPacker(
            max_tokens=config.max_context_tokens,
            max_target_options=config.max_target_options,
            max_diff_hunks=config.max_diff_hunks,
            max_diff_lines_per_hunk=config.max_diff_lines_per_hunk,
            max_doc_characters=config.max_doc_characters,
            max_regions=config.max_regions,
            max_review_comments=config.max_review_comments,
            max_reviews=config.max_reviews,
            max_commit_messages=config.max_commit_messages,
            max_issues=config.max_issues,
            max_issue_comments=config.max_issue_comments,
            max_docs=config.max_docs,
            max_code_lines=config.max_code_lines,
        )
        self.evidence_validator = EvidenceValidator(
            store, require_graph=config.require_graph_evidence
        )
        self.link_validator = GraphLinkValidator(index)
        self.normalizer = IntentNormalizer(config)
        self.deduplicator = deduplicator or Deduplicator(
            threshold=config.duplicate_similarity_threshold
        )
        self.duplicate_judge = duplicate_judge or DuplicateJudge(self.client)
        self.incident_extractor = IncidentExtractor()
        self.participant_extractor = ParticipantExtractor()
        self.supersession = SupersessionDetector()
        self.supersession_judge = supersession_judge or SupersessionJudge(
            self.client
        )
        self.extractor = DecisionExtractor()
        self.rules = ExtractionRuleValidator()

    # -- entry point ----------------------------------------------------
    def run(self, bundles: list[PrEvidenceBundle]) -> IntentRunResult:
        review = ReviewQueue(repo=self.config.repo, clock=self.clock)
        summary = IntentSummary(prs_scanned=len(bundles))
        accepted: list[IntentBase] = []
        accepted_ids: set[str] = set()
        incidents: dict[str, IncidentIntent] = {}
        validations: list[EvidenceValidationResult] = []

        for bundle in sorted(bundles, key=lambda item: item.pr_number):
            candidates = self.detector.detect(bundle)
            for candidate in candidates:
                _count(summary.candidates, candidate.candidate_class.value)
            incident_items = self.incident_extractor.extract(bundle)
            for incident in incident_items:
                incidents[incident.id] = _merge_incident(
                    incidents.get(incident.id), incident
                )

            actionable = [
                candidate
                for candidate in candidates
                if candidate.actionable
                and candidate.candidate_class is not CandidateClass.INCIDENT_CANDIDATE
            ]
            if not actionable:
                for candidate in candidates:
                    review.add(
                        ReviewReason.LOW_SIGNAL,
                        pr_number=bundle.pr_number,
                        candidate_class=candidate.candidate_class.value,
                        claim=str(bundle.pr.payload.get("title") or ""),
                        issues=candidate.reasons,
                    )
                continue

            primary = max(
                actionable,
                key=lambda item: (
                    item.score,
                    _CANDIDATE_PRIORITY.get(item.candidate_class, 0),
                ),
            )
            created = self._extract_bundle(
                bundle, primary, review, validations
            )
            for intent in created:
                if intent.id in accepted_ids:
                    review.add(
                        ReviewReason.POSSIBLE_DUPLICATE,
                        pr_number=bundle.pr_number,
                        candidate_class=primary.candidate_class.value,
                        claim=intent.claim,
                        issues=[f"intent id {intent.id} already accepted"],
                    )
                    continue
                accepted_ids.add(intent.id)
                accepted.append(intent)

        pr_dates = {
            bundle.pr_number: str(bundle.pr.payload.get("merged_at") or "")
            for bundle in bundles
        }
        evidence_by_id = {
            item.record_id: item
            for bundle in bundles
            for item in bundle.all_items()
        }
        evidence_by_pr = {
            bundle.pr_number: bundle.all_items() for bundle in bundles
        }
        duplicate_result = self.deduplicator.adjudicate(
            accepted,
            evidence_by_id=evidence_by_id,
            evidence_by_pr=evidence_by_pr,
            classifier=self.duplicate_judge.judge,
        )
        accepted = duplicate_result.intents
        for decision in duplicate_result.decisions:
            if decision.applied == "review":
                review.add(
                    ReviewReason.POSSIBLE_DUPLICATE,
                    pr_number=_intent_pr(accepted, decision.later_id),
                    claim=_intent_claim(accepted, decision.later_id),
                    issues=[
                        f"{decision.earlier_id} ~ {decision.later_id}: "
                        f"{decision.explanation}",
                        (
                            "high-impact merge requires human review"
                            if decision.high_impact
                            and decision.relationship == "merge"
                            else "duplicate adjudication needs review"
                        ),
                    ],
                )
            elif decision.relationship == "conflicts":
                review.add(
                    ReviewReason.CONTESTED,
                    pr_number=_intent_pr(accepted, decision.later_id),
                    claim=_intent_claim(accepted, decision.later_id),
                    issues=[
                        f"{decision.earlier_id} conflicts with "
                        f"{decision.later_id}: {decision.explanation}"
                    ],
                )

        supersession_result = self.supersession.detect(
            accepted,
            pr_dates=pr_dates,
            evidence_by_id=evidence_by_id,
            evidence_by_pr=evidence_by_pr,
            classifier=self.supersession_judge.judge,
        )
        accepted = supersession_result.intents
        for supersession_decision in supersession_result.decisions:
            if supersession_decision.relationship != "insufficient_evidence":
                continue
            review.add(
                ReviewReason.INSUFFICIENT_EVIDENCE,
                pr_number=_intent_pr(
                    accepted, supersession_decision.later_decision_id
                ),
                claim=_intent_claim(
                    accepted, supersession_decision.later_decision_id
                ),
                issues=[
                    f"{supersession_decision.earlier_decision_id} -> "
                    f"{supersession_decision.later_decision_id}: "
                    f"{supersession_decision.explanation}"
                ],
            )
        for update in supersession_result.status_updates:
            if update.status.value != "contested":
                continue
            review.add(
                ReviewReason.CONTESTED,
                pr_number=_intent_pr(accepted, update.intent_id),
                claim=_intent_claim(accepted, update.intent_id),
                issues=[update.reason],
            )

        bundles_by_pr = {bundle.pr_number: bundle for bundle in bundles}
        participants: list[Participant] = []
        for pr_number in sorted(bundles_by_pr):
            pr_intents = [intent for intent in accepted if intent.pr_number == pr_number]
            if not pr_intents:
                continue
            participants.extend(
                self.participant_extractor.extract(
                    bundles_by_pr[pr_number], pr_intents
                )
            )
        participants = _dedupe_participants(participants)

        evidence_refs = self._evidence_refs(
            accepted, list(incidents.values()), bundles_by_pr
        )
        graph = build_intent_graph(
            repo=self.config.repo,
            intents=accepted,
            evidence_refs=evidence_refs,
            incidents=list(incidents.values()),
            participants=participants,
            index=self.index,
            supersession_edges=supersession_result.edges,
            conflict_edges=duplicate_result.conflict_edges,
        )
        for intent in accepted:
            _count(summary.accepted, intent.kind.value)
        summary.proposals = sum(summary.accepted.values())
        summary.review_items = len(review.items)
        summary.rejected = sum(
            count
            for reason, count in review.counts().items()
            if reason != ReviewReason.LOW_SIGNAL.value
        )

        result = IntentRunResult(
            repo=self.config.repo,
            intents=accepted,
            incidents=sorted(incidents.values(), key=lambda item: item.id),
            participants=participants,
            evidence_refs=evidence_refs,
            graph=graph,
            review_queue=review,
            summary=summary,
            supersession_edges=supersession_result.edges,
            supersession_decisions=supersession_result.decisions,
            duplicate_merges=duplicate_result.merges,
            duplicate_decisions=duplicate_result.decisions,
            conflict_edges=duplicate_result.conflict_edges,
            status_updates=supersession_result.status_updates,
            evidence_validations=validations,
        )
        if self.logger is not None:
            self.logger.info(
                "intent_run_complete",
                prs=summary.prs_scanned,
                accepted=summary.proposals,
                review_items=summary.review_items,
            )
        return result

    # -- one PR ---------------------------------------------------------
    def _extract_bundle(
        self,
        bundle: PrEvidenceBundle,
        candidate: Candidate,
        review: ReviewQueue,
        validations: list[EvidenceValidationResult],
    ) -> list[IntentBase]:
        pack = self.packer.pack(
            bundle,
            candidate,
            target_options=self.index.options_for_paths(bundle.changed_paths()),
        )
        try:
            outcome = self.extractor.extract(pack, self.client)
        except AppError as error:
            review.add(
                ReviewReason.LLM_ERROR,
                pr_number=bundle.pr_number,
                candidate_class=candidate.candidate_class.value,
                issues=[str(error)],
            )
            return []
        except Exception as error:  # noqa: BLE001 - one bad call never stops a run
            review.add(
                ReviewReason.LLM_ERROR,
                pr_number=bundle.pr_number,
                candidate_class=candidate.candidate_class.value,
                issues=[f"{type(error).__name__}: {error}"],
            )
            return []
        if not outcome.ok:
            reason = (
                ReviewReason.INVALID_SCHEMA
                if any("schema:" in issue for issue in outcome.issues)
                else ReviewReason.LLM_ERROR
            )
            review.add(
                reason,
                pr_number=bundle.pr_number,
                candidate_class=candidate.candidate_class.value,
                issues=outcome.issues,
                raw_output=outcome.raw,
            )
            return []

        allowed = set(pack.evidence_ids())
        graph_ids = set(self.index.node_ids)
        accepted: list[IntentBase] = []
        for index, proposal in enumerate(outcome.proposals):
            if not isinstance(proposal, ExtractedCandidate):
                review.add(
                    ReviewReason.INVALID_SCHEMA,
                    pr_number=bundle.pr_number,
                    candidate_class=candidate.candidate_class.value,
                    issues=[
                        f"unexpected proposal type {type(proposal).__name__}"
                    ],
                )
                continue
            intent = self._validate_proposal(
                proposal,
                bundle=bundle,
                candidate=candidate,
                pack=pack,
                allowed=allowed,
                graph_ids=graph_ids,
                decision_id=f"{bundle.repo}:pr:{bundle.pr_number}:candidate:{index}",
                missing_evidence=outcome.missing_evidence,
                review=review,
                validations=validations,
            )
            if intent is not None:
                accepted.append(intent)

        if not accepted and outcome.missing_evidence:
            review.add(
                ReviewReason.INSUFFICIENT_EVIDENCE,
                pr_number=bundle.pr_number,
                candidate_class=candidate.candidate_class.value,
                issues=outcome.missing_evidence,
                raw_output=outcome.raw,
            )
        return accepted

    def _validate_proposal(
        self,
        proposal: ExtractedCandidate,
        *,
        bundle: PrEvidenceBundle,
        candidate: Candidate,
        pack: EvidencePack,
        allowed: set[str],
        graph_ids: set[str],
        decision_id: str,
        missing_evidence: list[str],
        review: ReviewQueue,
        validations: list[EvidenceValidationResult],
    ) -> IntentBase | None:
        target_validation = self.link_validator.validate_targets(
            proposal.targets,
            prefer_paths=bundle.changed_paths(),
            prefer_commit=bundle.latest_commit_sha(),
        )
        validation = self.evidence_validator.validate_candidate(
            proposal,
            bundle=bundle,
            allowed_ids=allowed,
            graph_evidence_ids=graph_ids,
            target_validation=target_validation,
            decision_id=decision_id,
            resolve_citation=pack.resolve_citation,
            allow_target_downgrade=self.config.allow_target_downgrade,
            reject_unsupported_participants=(
                self.config.reject_unsupported_participants
            ),
        )
        validations.append(validation.result)

        if not validation.accepted:
            review.add(
                _review_reason(validation),
                pr_number=bundle.pr_number,
                candidate_class=candidate.candidate_class.value,
                claim=proposal.claim,
                issues=validation.issue_texts,
                evidence=list(validation.evidence.accepted_ids),
                targets=[target.graph_key for target in validation.targets.targets],
                proposed_confidence=validation.confidence.level,
            )
            return None

        evidence_items = [
            item
            for evidence_id in validation.evidence.accepted_ids
            if (item := bundle.items_by_id().get(evidence_id)) is not None
        ]
        # Phase 4 has already rejected a candidate with no resolvable target
        # and downgraded a partial set to an observation. The rule gate only
        # needs the resolved targets, not the duplicate target error.
        rule_targets = TargetValidation(
            targets=target_validation.targets,
            dropped=target_validation.dropped,
        )
        rule_result = self.rules.validate(
            proposal,
            bundle=bundle,
            evidence_items=evidence_items,
            target_validation=rule_targets,
        )
        if not rule_result.ok:
            review.add(
                ReviewReason.RULE_VIOLATION,
                pr_number=bundle.pr_number,
                candidate_class=candidate.candidate_class.value,
                claim=proposal.claim,
                issues=rule_result.issue_texts,
                evidence=list(validation.evidence.accepted_ids),
                targets=[target.graph_key for target in validation.targets.targets],
                proposed_confidence=validation.confidence.level,
            )
            return None

        status = rule_result.adjusted_status or proposal.status
        if validation.extraction_status is not None and status not in {
            "rejected",
            "reversed",
        }:
            status = validation.extraction_status.value
        proposal = proposal.model_copy(update={"status": status})

        try:
            intent = self._normalize(
                proposal,
                bundle=bundle,
                evidence_ids=validation.evidence.accepted_ids,
                target_validation=target_validation,
                confidence=validation.confidence,
                candidate=candidate,
                missing_evidence=missing_evidence,
            )
        except Exception as error:  # noqa: BLE001 - a bad proposal is reviewable
            review.add(
                ReviewReason.INVALID_SCHEMA,
                pr_number=bundle.pr_number,
                candidate_class=candidate.candidate_class.value,
                claim=proposal.claim,
                issues=[f"{type(error).__name__}: {error}"],
            )
            return None

        if (
            validation.canonical_status is not None
            and intent.status is DecisionStatus.ACTIVE
        ):
            intent = intent.model_copy(
                update={"status": validation.canonical_status}
            )
        if validation.result.final_status is FinalStatus.OBSERVATION:
            review.add(
                ReviewReason.UNRESOLVED_TARGET,
                pr_number=bundle.pr_number,
                candidate_class=candidate.candidate_class.value,
                claim=proposal.claim,
                issues=validation.targets.issues
                or ["some targets were unresolved; recorded as an observation"],
                evidence=list(validation.evidence.accepted_ids),
                targets=[target.graph_key for target in validation.targets.targets],
                proposed_confidence=validation.confidence.level,
            )
        if validation.canonical_status is DecisionStatus.CONTESTED:
            review.add(
                ReviewReason.CONTESTED,
                pr_number=bundle.pr_number,
                candidate_class=candidate.candidate_class.value,
                claim=proposal.claim,
                issues=validation.notes,
                evidence=list(validation.evidence.accepted_ids),
                targets=[target.graph_key for target in validation.targets.targets],
                proposed_confidence=validation.confidence.level,
            )
        return intent

    def _normalize(
        self,
        proposal: ExtractedCandidate,
        *,
        bundle: PrEvidenceBundle,
        evidence_ids: list[str],
        target_validation: Any,
        confidence: ConfidenceResult,
        candidate: Candidate,
        missing_evidence: list[str],
    ) -> IntentBase:
        source_candidate = _SOURCE_CANDIDATE_BY_DECISION_TYPE.get(
            proposal.decision_type, CandidateClass.DECISION_CANDIDATE.value
        )
        return self.normalizer.candidate(
            proposal,
            bundle=bundle,
            evidence_ids=evidence_ids,
            target_validation=target_validation,
            confidence=confidence,
            index=self.index,
            source_candidate=source_candidate,
            missing_evidence=missing_evidence,
        )

    # -- evidence wrappers ---------------------------------------------
    def _evidence_refs(
        self,
        intents: list[IntentBase],
        incidents: list[IncidentIntent],
        bundles_by_pr: dict[int, PrEvidenceBundle],
    ) -> list[EvidenceRef]:
        items: dict[str, EvidenceItem] = {}
        for bundle in bundles_by_pr.values():
            items.update(bundle.items_by_id())
        needed: set[str] = set()
        for intent in intents:
            needed.update(intent.evidence_ids)
        for incident in incidents:
            needed.update(incident.evidence_ids)
        refs: list[EvidenceRef] = []
        for evidence_id in sorted(needed):
            bundle = _bundle_for_evidence(evidence_id, items, bundles_by_pr)
            refs.append(build_evidence_ref(evidence_id, bundle))
        return refs


def _bundle_for_evidence(
    evidence_id: str,
    items: dict[str, EvidenceItem],
    bundles_by_pr: dict[int, PrEvidenceBundle],
) -> PrEvidenceBundle:
    item = items.get(evidence_id)
    if item is not None and item.pr_number in bundles_by_pr:
        return bundles_by_pr[item.pr_number]
    if item is not None:
        # Person/doc/issue records can carry no PR number; fall back to the
        # unique bundle without inventing provenance.
        for bundle in bundles_by_pr.values():
            if evidence_id in bundle.items_by_id():
                return bundle
    raise InvalidRecordError(
        f"no evidence bundle contains {evidence_id!r}", component="intent"
    )


def _merge_incident(
    existing: IncidentIntent | None, incoming: IncidentIntent
) -> IncidentIntent:
    if existing is None:
        return incoming
    return incoming.model_copy(
        update={
            "pr_numbers": sorted(set(existing.pr_numbers) | set(incoming.pr_numbers)),
            "evidence_ids": sorted(
                set(existing.evidence_ids) | set(incoming.evidence_ids)
            ),
        }
    )


def _dedupe_participants(participants: list[Participant]) -> list[Participant]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[Participant] = []
    for participant in participants:
        key = (participant.person_id, participant.role, participant.target_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(participant)
    return sorted(unique, key=lambda item: (item.person_id, item.role, item.target_id))


def _review_reason(validation: CandidateValidation) -> ReviewReason:
    codes = set(validation.reason_codes)
    if "schema_invalid" in codes:
        return ReviewReason.INVALID_SCHEMA
    if codes & {
        "no_evidence",
        "duplicate_evidence",
        "evidence_outside_pack",
        "evidence_not_found",
        "evidence_missing_from_graph",
        "graph_index_unavailable",
    }:
        return ReviewReason.INVALID_EVIDENCE
    if codes & {"no_structural_target", "target_partially_unresolved"}:
        return ReviewReason.UNRESOLVED_TARGET
    if "unsupported_claim" in codes:
        return ReviewReason.UNSUPPORTED_CLAIM
    if "temporal_invalid" in codes:
        return ReviewReason.INVALID_TEMPORAL
    if "reversal_without_later_change" in codes:
        return ReviewReason.INSUFFICIENT_EVIDENCE
    if "participant_unsupported" in codes:
        return ReviewReason.UNSUPPORTED_CLAIM
    return ReviewReason.RULE_VIOLATION


def _count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _intent_pr(intents: list[IntentBase], intent_id: str) -> int | None:
    for intent in intents:
        if intent.id == intent_id:
            return intent.pr_number
    return None


def _intent_claim(intents: list[IntentBase], intent_id: str) -> str:
    for intent in intents:
        if intent.id == intent_id:
            return intent.claim
    return ""


def _log_retry(logger: Any | None) -> Any | None:
    if logger is None:
        return None

    def on_retry(attempt: int, error: Exception, delay: float) -> None:
        logger.warning(
            "llm_retry",
            attempt=attempt,
            error=type(error).__name__,
            delay=delay,
        )

    return on_retry


__all__ = ["IntentPipeline", "IntentRunResult"]
