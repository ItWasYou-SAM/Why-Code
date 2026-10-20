"""Constraint-focused candidate extraction.

The Phase 3 provider schema is unified, so this call returns the same
``candidates`` envelope as decision extraction. The pipeline normally makes a
single unified call per PR; this extractor remains available for a focused
replay that filters to ``decision_type == "constraint"``.
"""

from __future__ import annotations

from app.intent.context.prompt_render import ExtractionKind
from app.intent.extraction.base import BaseExtractor
from app.intent.llm.schemas import CandidateExtraction


class ConstraintExtractor(BaseExtractor):
    kind = ExtractionKind.CONSTRAINT
    schema_model = CandidateExtraction
    envelope_field = "candidates"


__all__ = ["ConstraintExtractor"]
