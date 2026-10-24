"""Phase 10 metrics for an evidence-backed decision run.

Every metric exposes numerator and denominator. A metric with no applicable
cases is ``null`` rather than ``0``: "not checked" and "checked and wrong" are
different claims (D-0032). Precision is calculated only over predictions that
belong to the golden set's PRs; the golden set is not an exhaustive annotation
of the whole repository, so unrelated real decisions must not be called false
positives.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.intent.evaluation.matching import (
    CaseMatch,
    match_cases,
    normalize_target_key,
)
from app.intent.evaluation.models import GoldenCase, PredictionSet

_METRIC_NOTE = (
    "A null value means the metric had no applicable cases; it is not zero."
)


class MetricStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    numerator: int = Field(default=0, ge=0)
    denominator: int = Field(default=0, ge=0)
    value: float | None = None

    @classmethod
    def ratio(cls, numerator: int, denominator: int) -> MetricStats:
        return cls(
            numerator=numerator,
            denominator=denominator,
            value=(
                round(numerator / denominator, 4) if denominator else None
            ),
        )


class Metrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_recall: MetricStats
    decision_precision: MetricStats
    decision_recall: MetricStats
    evidence_precision: MetricStats
    evidence_recall: MetricStats
    target_precision: MetricStats
    target_recall: MetricStats
    supersession_precision: MetricStats
    supersession_recall: MetricStats
    refusal_correctness: MetricStats
    false_refusal_rate: MetricStats
    status_accuracy: MetricStats


class Counts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    golden_cases: int = 0
    expected_decisions: int = 0
    expected_refusals: int = 0
    predictions_in_scope: int = 0
    predictions_out_of_scope: int = 0
    matched_decisions: int = 0
    false_positives: int = 0
    expected_supersession_edges: int = 0
    scored_supersession_edges: int = 0
    unscored_supersession_edges: int = 0


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    corpus: str
    question: str
    expected_status: str
    matched: bool
    prediction_id: str | None = None
    status_match: bool | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    extra_evidence: list[str] = Field(default_factory=list)
    missing_targets: list[str] = Field(default_factory=list)
    extra_targets: list[str] = Field(default_factory=list)
    supersession_expected: bool = False
    supersession_correct: bool | None = None
    matched_supersession_edge: str | None = None
    notes: str = ""


class EvaluationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str | None
    corpus: str
    golden_path: str
    predictions_path: str
    case_count: int
    metrics: Metrics
    counts: Counts
    cases: list[CaseResult]
    unscored_supersession_edges: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def evaluate_corpus(
    *,
    cases: list[GoldenCase],
    predictions: PredictionSet,
    repo: str | None = None,
    corpus: str = "all",
    golden_path: str = "",
    predictions_path: str = "",
) -> EvaluationReport:
    if not cases:
        raise ValueError("cannot evaluate an empty golden corpus")

    expected_prs = {pr for case in cases for pr in case.expected_prs}
    expected_decision_cases = [
        case for case in cases if case.expected_status != "refused"
    ]
    refused_cases = [case for case in cases if case.expected_status == "refused"]

    in_scope = [
        decision
        for decision in predictions.decisions
        if decision.pr_number is None or decision.pr_number in expected_prs
    ]
    out_of_scope = len(predictions.decisions) - len(in_scope)

    matches = match_cases(expected_decision_cases, in_scope)
    matched_by_case = matches.by_case()
    matched_prediction_ids = {
        match.decision.id for match in matches.matches
    }

    decision_prs = {
        pr for case in expected_decision_cases for pr in case.expected_prs
    }
    found_prs = {
        decision.pr_number
        for decision in in_scope
        if decision.pr_number is not None and decision.pr_number in decision_prs
    }

    evidence_hits = 0
    evidence_cited = 0
    evidence_expected = 0
    target_hits = 0
    target_predicted = 0
    target_expected = 0
    status_hits = 0
    status_checked = 0
    for match in matches.matches:
        expected_evidence = set(match.case.expected_evidence)
        predicted_evidence = set(match.decision.evidence_ids)
        evidence_hits += len(expected_evidence & predicted_evidence)
        evidence_cited += len(predicted_evidence)
        evidence_expected += len(expected_evidence)

        expected_targets = {
            normalize_target_key(value) for value in match.case.expected_targets
        }
        predicted_targets = {
            normalize_target_key(target.as_key())
            for target in match.decision.targets
        }
        target_hits += len(expected_targets & predicted_targets)
        target_predicted += len(predicted_targets)
        target_expected += len(expected_targets)

        expected_status = _status_label(match.case.expected_status)
        status_checked += 1
        if _status_label(match.decision.status) == expected_status:
            status_hits += 1

    supersession, unscored, supersession_correct_by_case = _supersession_metrics(
        expected_cases=expected_decision_cases,
        matched_by_case=matched_by_case,
        predictions=predictions,
    )

    refusal_correct = sum(
        1
        for case in refused_cases
        if not any(
            decision.pr_number is not None
            and decision.pr_number in case.expected_prs
            for decision in in_scope
        )
    )

    if predictions.refusals_provided:
        false_refusals = sum(
            1
            for case in expected_decision_cases
            if any(pr in predictions.refusals for pr in case.expected_prs)
        )
        false_refusal_rate = MetricStats.ratio(
            false_refusals, len(expected_decision_cases)
        )
    else:
        false_refusal_rate = MetricStats.ratio(0, 0)

    metrics = Metrics(
        candidate_recall=MetricStats.ratio(len(found_prs), len(decision_prs)),
        decision_precision=MetricStats.ratio(
            len(matches.matches), len(in_scope)
        ),
        decision_recall=MetricStats.ratio(
            len(matches.matches), len(expected_decision_cases)
        ),
        evidence_precision=MetricStats.ratio(evidence_hits, evidence_cited),
        evidence_recall=MetricStats.ratio(evidence_hits, evidence_expected),
        target_precision=MetricStats.ratio(target_hits, target_predicted),
        target_recall=MetricStats.ratio(target_hits, target_expected),
        supersession_precision=supersession[0],
        supersession_recall=supersession[1],
        refusal_correctness=MetricStats.ratio(
            refusal_correct, len(refused_cases)
        ),
        false_refusal_rate=false_refusal_rate,
        status_accuracy=MetricStats.ratio(status_hits, status_checked),
    )

    results = [
        _case_result(
            case,
            matched_by_case.get(case.case_id),
            supersession_correct_by_case.get(case.case_id),
        )
        for case in cases
    ]
    counts = Counts(
        golden_cases=len(cases),
        expected_decisions=len(expected_decision_cases),
        expected_refusals=len(refused_cases),
        predictions_in_scope=len(in_scope),
        predictions_out_of_scope=out_of_scope,
        matched_decisions=len(matches.matches),
        false_positives=len(in_scope) - len(matched_prediction_ids),
        expected_supersession_edges=sum(
            1
            for case in expected_decision_cases
            if case.expected_supersession is not None
        ),
        scored_supersession_edges=supersession[2],
        unscored_supersession_edges=len(unscored),
    )
    notes = [
        _METRIC_NOTE,
        "Precision counts only predictions from the golden set's PRs.",
    ]
    if out_of_scope:
        notes.append(
            f"{out_of_scope} prediction(s) outside the golden PR set were ignored."
        )
    if not predictions.refusals_provided:
        notes.append(
            "No explicit refusals were supplied; false refusal rate is not checked."
        )
    return EvaluationReport(
        repo=repo,
        corpus=corpus,
        golden_path=golden_path,
        predictions_path=predictions_path,
        case_count=len(cases),
        metrics=metrics,
        counts=counts,
        cases=results,
        unscored_supersession_edges=unscored,
        notes=notes,
    )


def _supersession_metrics(
    *,
    expected_cases: list[GoldenCase],
    matched_by_case: dict[str, CaseMatch],
    predictions: PredictionSet,
) -> tuple[tuple[MetricStats, MetricStats, int], list[str], dict[str, bool | None]]:
    case_by_id = {case.case_id: case for case in expected_cases}
    case_by_prediction: dict[str, GoldenCase] = {
        match.decision.id: case_by_id[case_id]
        for case_id, match in matched_by_case.items()
    }

    correct = 0
    scored = 0
    unscored: list[str] = []
    correct_by_case: dict[str, bool | None] = {
        case.case_id: (False if case.expected_supersession else None)
        for case in expected_cases
    }
    for edge in predictions.edges:
        later = case_by_prediction.get(edge.start_id)
        earlier = case_by_prediction.get(edge.end_id)
        if later is None or earlier is None:
            unscored.append(
                f"{edge.rel_type}:{edge.start_id}->{edge.end_id}"
            )
            continue
        scored += 1
        expected = later.expected_supersession
        edge_correct = bool(
            expected is not None
            and edge.rel_type == expected.relationship
            and earlier.case_id == expected.target_case_id
        )
        if edge_correct:
            correct += 1
        if expected is not None:
            correct_by_case[later.case_id] = edge_correct

    expected_edges = sum(
        1 for case in expected_cases if case.expected_supersession is not None
    )
    return (
        (
            MetricStats.ratio(correct, scored),
            MetricStats.ratio(correct, expected_edges),
            scored,
        ),
        unscored,
        correct_by_case,
    )


def _case_result(
    case: GoldenCase,
    match: CaseMatch | None,
    supersession_correct: bool | None,
) -> CaseResult:
    if match is None:
        return CaseResult(
            case_id=case.case_id,
            corpus=case.corpus,
            question=case.question,
            expected_status=case.expected_status,
            matched=False,
            supersession_expected=case.expected_supersession is not None,
            supersession_correct=supersession_correct,
            notes=case.notes,
        )
    decision = match.decision
    expected_evidence = set(case.expected_evidence)
    predicted_evidence = set(decision.evidence_ids)
    expected_targets = {
        normalize_target_key(value) for value in case.expected_targets
    }
    predicted_targets = {
        normalize_target_key(target.as_key()) for target in decision.targets
    }
    return CaseResult(
        case_id=case.case_id,
        corpus=case.corpus,
        question=case.question,
        expected_status=case.expected_status,
        matched=True,
        prediction_id=decision.id,
        status_match=(
            _status_label(decision.status) == _status_label(case.expected_status)
        ),
        missing_evidence=sorted(expected_evidence - predicted_evidence),
        extra_evidence=sorted(predicted_evidence - expected_evidence),
        missing_targets=sorted(expected_targets - predicted_targets),
        extra_targets=sorted(predicted_targets - expected_targets),
        supersession_expected=case.expected_supersession is not None,
        supersession_correct=supersession_correct,
        notes=case.notes,
    )


def _status_label(value: str) -> str:
    normalised = value.strip().lower()
    return {
        "active": "accepted",
        "accepted": "accepted",
        "superseded": "accepted",
        "reversed": "reversed",
        "revised": "accepted",
        "contested": "contested",
        "uncertain": "uncertain",
        "rejected": "rejected",
    }.get(normalised, normalised)


__all__ = [
    "CaseResult",
    "Counts",
    "EvaluationReport",
    "MetricStats",
    "Metrics",
    "evaluate_corpus",
]
