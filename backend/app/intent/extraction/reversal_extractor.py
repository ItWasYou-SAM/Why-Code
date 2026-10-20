"""Reversal-focused candidate extraction.

The Phase 3 provider schema is unified, so this call returns the same
``candidates`` envelope as decision extraction. Reversal *relationships* are
still computed by ``SupersessionDetector``; the model only proposes the
reversal claim and its evidence.
"""

from __future__ import annotations

from app.intent.context.prompt_render import ExtractionKind
from app.intent.extraction.base import BaseExtractor
from app.intent.llm.schemas import CandidateExtraction


class ReversalExtractor(BaseExtractor):
    kind = ExtractionKind.REVERSAL
    schema_model = CandidateExtraction
    envelope_field = "candidates"


__all__ = ["ReversalExtractor"]
