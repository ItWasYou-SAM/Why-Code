"""Top-level evaluation runner and per-corpus aggregation."""

from __future__ import annotations

from pathlib import Path

from app.core.errors import InvalidRecordError
from app.intent.evaluation.golden import load_golden_cases
from app.intent.evaluation.metrics import EvaluationReport, evaluate_corpus
from app.intent.evaluation.models import GoldenCase, PredictionSet
from app.intent.evaluation.predictions import load_predictions


def evaluate_files(
    *,
    predictions_path: str | Path,
    golden_path: str | Path | None = None,
    repo: str | None = None,
    corpus: str = "all",
) -> dict[str, EvaluationReport]:
    """Load both sides and return one report per selected corpus."""

    cases = load_golden_cases(golden_path)
    predictions = load_predictions(predictions_path)
    return evaluate_cases(
        cases=cases,
        predictions=predictions,
        repo=repo,
        corpus=corpus,
        golden_path=str(golden_path) if golden_path is not None else "",
        predictions_path=str(predictions_path),
    )


def evaluate_cases(
    *,
    cases: list[GoldenCase],
    predictions: PredictionSet,
    repo: str | None = None,
    corpus: str = "all",
    golden_path: str = "",
    predictions_path: str = "",
) -> dict[str, EvaluationReport]:
    corpora = sorted({case.corpus for case in cases})
    selected = corpora if corpus == "all" else [corpus]
    missing = [name for name in selected if name not in corpora]
    if missing:
        raise InvalidRecordError(
            f"unknown corpus {missing[0]!r}; expected one of {', '.join(corpora)}",
            component="intent.evaluation",
        )
    reports: dict[str, EvaluationReport] = {}
    for name in selected:
        subset = [case for case in cases if case.corpus == name]
        reports[name] = evaluate_corpus(
            cases=subset,
            predictions=predictions,
            repo=repo,
            corpus=name,
            golden_path=golden_path,
            predictions_path=predictions_path,
        )
    return reports


__all__ = ["evaluate_cases", "evaluate_files"]
