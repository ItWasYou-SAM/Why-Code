"""Phase 10: human-curated golden decisions and deterministic metrics."""

from app.intent.evaluation.golden import (
    default_golden_path,
    load_golden_cases,
    validate_golden_cases,
)
from app.intent.evaluation.matching import CaseMatch, MatchResult, match_cases
from app.intent.evaluation.metrics import (
    CaseResult,
    Counts,
    EvaluationReport,
    Metrics,
    MetricStats,
    evaluate_corpus,
)
from app.intent.evaluation.models import (
    GoldenCase,
    GoldenSupersession,
    PredictedDecision,
    PredictedEdge,
    PredictedTarget,
    PredictionSet,
)
from app.intent.evaluation.predictions import load_predictions, predictions_from_payload
from app.intent.evaluation.runner import evaluate_cases, evaluate_files

__all__ = [
    "CaseMatch",
    "CaseResult",
    "Counts",
    "EvaluationReport",
    "GoldenCase",
    "GoldenSupersession",
    "MatchResult",
    "MetricStats",
    "Metrics",
    "PredictedDecision",
    "PredictedEdge",
    "PredictedTarget",
    "PredictionSet",
    "default_golden_path",
    "evaluate_cases",
    "evaluate_corpus",
    "evaluate_files",
    "load_golden_cases",
    "load_predictions",
    "match_cases",
    "predictions_from_payload",
    "validate_golden_cases",
]
