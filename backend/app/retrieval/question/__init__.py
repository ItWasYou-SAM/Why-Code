"""Question understanding for the retrieval pipeline.

The public surface is deliberately small: the service gets an ``EntityIndex``
and a ``QuestionAnalyzer``. Phase 1 internals (catalog loader, classifier,
extractor, query builder) remain importable for tests and the offline CLI.
"""

from __future__ import annotations

from app.retrieval.question.entity_extractor import (
    EntityExtractor,
    EntityIndex,
    KnownEntity,
    QuestionAnalyzer,
)
from app.retrieval.question.question_classifier import QuestionClassifier

__all__ = [
    "EntityExtractor",
    "EntityIndex",
    "KnownEntity",
    "QuestionAnalyzer",
    "QuestionClassifier",
]
