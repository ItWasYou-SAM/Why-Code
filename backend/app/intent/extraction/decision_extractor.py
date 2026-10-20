"""Unified decision/candidate extraction call (Phase 3)."""

from __future__ import annotations

from app.intent.context.prompt_render import ExtractionKind
from app.intent.extraction.base import BaseExtractor
from app.intent.llm.schemas import CandidateExtraction


class DecisionExtractor(BaseExtractor):
    kind = ExtractionKind.DECISION
    schema_model = CandidateExtraction
    envelope_field = "candidates"


__all__ = ["DecisionExtractor"]
