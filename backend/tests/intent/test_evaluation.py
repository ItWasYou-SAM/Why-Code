"""Phase 10 golden set and metric contract tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from intent_helpers import REPO, records, structural_records
from pydantic import ValidationError

from app.intent.cli import main
from app.intent.evaluation import (
    GoldenCase,
    PredictedDecision,
    PredictedEdge,
    PredictedTarget,
    PredictionSet,
    evaluate_corpus,
    load_golden_cases,
    predictions_from_payload,
)
from app.intent.evaluation.golden import default_golden_path
from app.intent.evaluation.matching import normalize_target_key
from app.intent.validation.graph_link_validator import GraphIndex

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_cases() -> list[GoldenCase]:
    return [
        case
        for case in load_golden_cases(default_golden_path())
        if case.corpus == "fixture"
    ]


def predictions_from_expected(
    cases: list[GoldenCase], *, extra: list[PredictedDecision] | None = None
) -> PredictionSet:
    decisions: list[PredictedDecision] = []
    edges: list[PredictedEdge] = []
    for case in cases:
        if case.expected_status == "refused":
            continue
        decision_id = f"pred_{case.case_id}"
        decisions.append(
            PredictedDecision(
                id=decision_id,
                pr_number=case.expected_prs[0],
                claim=case.expected_decision or "",
                status=_status(case.expected_status),
                evidence_ids=list(case.expected_evidence),
                targets=[
                    _target(value) for value in case.expected_targets
                ],
            )
        )
        supersession = case.expected_supersession
        if supersession is not None:
            edges.append(
                PredictedEdge(
                    rel_type=supersession.relationship,
                    start_id=decision_id,
                    end_id=f"pred_{supersession.target_case_id}",
                )
            )
    if extra:
        decisions.extend(extra)
    return PredictionSet(
        decisions=decisions,
        edges=edges,
        refusals=set(),
        refusals_provided=True,
    )


def _target(value: str) -> PredictedTarget:
    kind, _, identifier = value.partition(":")
    return PredictedTarget(type=kind, identifier=identifier)


def _status(value: str) -> str:
    return "active" if value == "accepted" else value


def report_for(
    cases: list[GoldenCase], predictions: PredictionSet
) -> Any:
    return evaluate_corpus(
        cases=cases,
        predictions=predictions,
        repo=REPO,
        corpus=cases[0].corpus,
    )


class TestGoldenSet:
    def test_suite_has_twenty_cases_and_both_corpora(self) -> None:
        cases = load_golden_cases(default_golden_path())
        assert len(cases) == 20
        assert 15 <= len(cases) <= 25
        assert {case.corpus for case in cases} == {"fixture", "httpx-public"}
        assert sum(1 for case in cases if case.corpus == "fixture") == 5
        assert sum(1 for case in cases if case.corpus == "httpx-public") == 15

    def test_seventeen_decisions_and_three_refusals(self) -> None:
        cases = load_golden_cases(default_golden_path())
        assert sum(1 for case in cases if case.expected_status != "refused") == 17
        assert sum(1 for case in cases if case.expected_status == "refused") == 3

    def test_refusal_cases_have_no_decision_payload(self) -> None:
        cases = load_golden_cases(default_golden_path())
        for case in cases:
            if case.expected_status == "refused":
                assert case.expected_decision is None
                assert case.expected_targets == []
                assert case.expected_evidence == []
            else:
                assert case.expected_decision
                assert case.expected_targets
                assert case.expected_evidence

    def test_fixture_evidence_ids_exist_in_the_fixture_store(self) -> None:
        known: set[str] = set()
        for name in ("retry_pr_evidence.json", "timeout_pr_evidence.json"):
            rows = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
            known.update(row["record_id"] for row in rows)
        for case in fixture_cases():
            assert set(case.expected_evidence) <= known, case.case_id

    def test_fixture_targets_exist_in_the_fixture_graph(self) -> None:
        fixture = [
            *records("retry_pr_evidence.json"),
            *records("timeout_pr_evidence.json"),
        ]
        options = GraphIndex.from_sources(
            repo=REPO,
            normalized_records=fixture,
            structural_records=structural_records(),
        ).options()
        for case in fixture_cases():
            for target in case.expected_targets:
                kind, _, identifier = target.partition(":")
                assert any(
                    option.type.name.lower() == kind
                    and (
                        option.identifier == identifier
                        or option.graph_key == identifier
                        or option.path == identifier
                    )
                    for option in options
                ), f"{case.case_id}: {target}"

    def test_supersession_references_an_existing_case(self) -> None:
        cases = load_golden_cases(default_golden_path())
        by_id = {case.case_id: case for case in cases}
        for case in cases:
            edge = case.expected_supersession
            if edge is None:
                continue
            assert edge.target_case_id in by_id
            assert by_id[edge.target_case_id].expected_status != "refused"


class TestPredictionLoading:
    def test_intent_batch_shape_is_accepted(self) -> None:
        payload = {
            "intents": [
                {
                    "id": "dec_1",
                    "kind": "decision",
                    "pr_number": None,
                    "claim": "Retries are limited to connection errors.",
                    "status": "active",
                    "evidence_ids": ["httpx:pr:1419"],
                    "targets": [
                        {"type": "Symbol", "identifier": "httpx._client.Client"}
                    ],
                }
            ],
            "supersession_edges": [
                {
                    "rel_type": "REVERSES",
                    "start_id": "dec_2",
                    "end_id": "dec_1",
                }
            ],
        }
        predictions = predictions_from_payload(payload)
        decision = predictions.decisions[0]
        assert decision.pr_number == 1419
        assert decision.targets[0].as_key() == "symbol:httpx._client.Client"
        assert predictions.edges[0].rel_type == "reverses"

    def test_duplicate_decision_ids_are_rejected(self) -> None:
        payload = {
            "decisions": [
                {"id": "same", "claim": "one"},
                {"id": "same", "claim": "two"},
            ]
        }
        with pytest.raises(ValidationError):
            predictions_from_payload(payload)


class TestPerfectFixtureRun:
    def test_every_metric_is_one(self) -> None:
        cases = fixture_cases()
        report = report_for(cases, predictions_from_expected(cases))
        metrics = report.metrics
        assert metrics.decision_recall.value == 1.0
        assert metrics.decision_precision.value == 1.0
        assert metrics.candidate_recall.value == 1.0
        assert metrics.evidence_precision.value == 1.0
        assert metrics.evidence_recall.value == 1.0
        assert metrics.target_precision.value == 1.0
        assert metrics.target_recall.value == 1.0
        assert metrics.supersession_precision.value == 1.0
        assert metrics.supersession_recall.value == 1.0
        assert metrics.status_accuracy.value == 1.0
        assert metrics.refusal_correctness.value is None
        assert metrics.false_refusal_rate.value == 0.0
        assert report.counts.false_positives == 0

    def test_case_results_expose_missing_and_extra_ids(self) -> None:
        cases = fixture_cases()
        report = report_for(cases, predictions_from_expected(cases))
        assert all(case.matched for case in report.cases)
        assert all(not case.missing_evidence for case in report.cases)
        assert all(not case.missing_targets for case in report.cases)
        reversal = next(
            case
            for case in report.cases
            if case.case_id == "revert_default_retry_behaviour"
        )
        assert reversal.supersession_expected is True
        assert reversal.supersession_correct is True


class TestMetricFailures:
    def test_missing_decisions_lower_recall_and_candidate_recall(self) -> None:
        cases = fixture_cases()
        perfect = predictions_from_expected(cases)
        kept = [
            decision
            for decision in perfect.decisions
            if decision.pr_number != 1419
        ]
        predictions = PredictionSet(
            decisions=kept,
            edges=perfect.edges,
            refusals_provided=True,
        )
        report = report_for(cases, predictions)
        assert report.metrics.candidate_recall.value == 0.5
        assert report.metrics.decision_recall.value == 0.6
        assert report.metrics.decision_precision.value == 1.0

    def test_false_positive_lowers_precision(self) -> None:
        cases = fixture_cases()
        perfect = predictions_from_expected(cases)
        extra = PredictedDecision(
            id="pred_false_positive",
            pr_number=1419,
            claim="Refresh the project logo and social preview.",
            evidence_ids=["httpx:pr:1419"],
            targets=[_target("file:docs/logo.png")],
        )
        report = report_for(
            cases,
            predictions_from_expected(cases, extra=[extra]),
        )
        assert report.metrics.decision_precision.value == round(5 / 6, 4)
        assert report.counts.false_positives == 1
        assert perfect.decisions

    def test_wrong_targets_lower_target_precision_and_recall(self) -> None:
        cases = fixture_cases()
        perfect = predictions_from_expected(cases)
        perfect.decisions[0].targets = [
            _target("symbol:httpx._client.Missing")
        ]
        report = report_for(cases, perfect)
        assert report.metrics.target_precision.value is not None
        assert report.metrics.target_precision.value < 1.0
        assert report.metrics.target_recall.value is not None
        assert report.metrics.target_recall.value < 1.0

    def test_wrong_evidence_lowers_evidence_precision_and_recall(self) -> None:
        cases = fixture_cases()
        perfect = predictions_from_expected(cases)
        perfect.decisions[0].evidence_ids = ["httpx:pr:9999"]
        report = report_for(cases, perfect)
        assert report.metrics.evidence_precision.value is not None
        assert report.metrics.evidence_precision.value < 1.0
        assert report.metrics.evidence_recall.value is not None
        assert report.metrics.evidence_recall.value < 1.0

    def test_wrong_supersession_relationship_is_incorrect(self) -> None:
        cases = fixture_cases()
        perfect = predictions_from_expected(cases)
        perfect.edges[0].rel_type = "supersedes"
        report = report_for(cases, perfect)
        assert report.metrics.supersession_precision.value == 0.0
        assert report.metrics.supersession_recall.value == 0.0

    def test_missing_supersession_edge_is_unscored_not_perfect(self) -> None:
        cases = fixture_cases()
        perfect = predictions_from_expected(cases)
        predictions = PredictionSet(
            decisions=perfect.decisions,
            edges=[],
            refusals_provided=True,
        )
        report = report_for(cases, predictions)
        assert report.metrics.supersession_precision.value is None
        assert report.metrics.supersession_recall.value == 0.0

    def test_out_of_scope_prediction_is_ignored(self) -> None:
        cases = fixture_cases()
        extra = PredictedDecision(
            id="pred_other_pr",
            pr_number=9999,
            claim="An unrelated decision from another PR.",
        )
        report = report_for(
            cases,
            predictions_from_expected(cases, extra=[extra]),
        )
        assert report.metrics.decision_precision.value == 1.0
        assert report.counts.predictions_out_of_scope == 1

    def test_null_is_not_zero_when_nothing_is_checkable(self) -> None:
        cases = fixture_cases()
        report = report_for(
            cases,
            PredictionSet(decisions=[], edges=[], refusals_provided=True),
        )
        assert report.metrics.decision_precision.value is None
        assert report.metrics.decision_recall.value == 0.0
        assert report.metrics.refusal_correctness.value is None


class TestRefusalCorrectness:
    def test_refusal_cases_pass_when_no_decision_is_emitted(self) -> None:
        cases = [
            case
            for case in load_golden_cases(default_golden_path())
            if case.corpus == "httpx-public"
            and case.expected_status == "refused"
        ]
        report = report_for(
            cases,
            PredictionSet(decisions=[], edges=[], refusals_provided=True),
        )
        assert report.metrics.refusal_correctness.value == 1.0
        assert report.metrics.decision_precision.value is None

    def test_a_decision_on_a_refusal_case_is_a_false_positive(self) -> None:
        cases = [
            case
            for case in load_golden_cases(default_golden_path())
            if case.corpus == "httpx-public"
            and case.expected_status == "refused"
        ]
        prediction = PredictedDecision(
            id="pred_invented",
            pr_number=3387,
            claim="Utility functions belong in the models module.",
            evidence_ids=["httpx:pr:3387"],
            targets=[_target("file:httpx/_models.py")],
        )
        report = report_for(
            cases,
            PredictionSet(
                decisions=[prediction],
                edges=[],
                refusals_provided=True,
            ),
        )
        assert report.metrics.refusal_correctness.value == round(2 / 3, 4)
        assert report.metrics.decision_precision.value == 0.0
        assert report.counts.false_positives == 1


class TestEvaluationCli:
    def test_evaluate_writes_a_report(self, tmp_path: Path) -> None:
        cases = fixture_cases()
        predictions = predictions_from_expected(cases)
        payload = {
            "decisions": [
                decision.model_dump(mode="json")
                for decision in predictions.decisions
            ],
            "edges": [edge.model_dump(mode="json") for edge in predictions.edges],
            "refusals": [],
        }
        input_path = tmp_path / "predictions.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        out_path = tmp_path / "report.json"
        code = main(
            [
                "evaluate",
                "--predictions",
                str(input_path),
                "--corpus",
                "fixture",
                "--out",
                str(out_path),
            ]
        )
        assert code == 0
        report = json.loads(out_path.read_text(encoding="utf-8"))
        assert report["corpus"] == "fixture"
        assert report["metrics"]["decision_recall"]["value"] == 1.0
        assert report["metrics"]["candidate_recall"]["value"] == 1.0

    def test_target_normalisation_accepts_repo_prefixed_config_keys(self) -> None:
        assert normalize_target_key("config_key:httpx.retries") == "config_key:retries"
