"""Constrained answer synthesis.

The model gets one evidence pack and one schema. A response that is not
valid JSON, does not match the schema, or claims an answer without citing
anything is retried once with a stricter prompt, then refused.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from app.core.errors import AppError
from app.retrieval.answer.prompt_templates import (
    ANSWER_SCHEMA,
    ANSWER_SYSTEM_PROMPT,
    build_user_prompt,
)
from app.retrieval.config import RetrievalConfig
from app.retrieval.context.evidence_packer import EvidencePack
from app.retrieval.models import (
    AnswerConfidence,
    AnswerDraft,
    AnswerStatus,
    QuestionUnderstanding,
)
from app.retrieval.ports import TextGenerator

ANSWER_SCHEMA_NAME = "retrieval_answer"
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class AnswerSynthesizer:
    def __init__(
        self,
        generator: TextGenerator,
        config: RetrievalConfig | None = None,
    ) -> None:
        self.generator = generator
        self.config = config or RetrievalConfig()

    def synthesize(
        self,
        *,
        pack: EvidencePack,
        understanding: QuestionUnderstanding,
        strict: bool = False,
    ) -> AnswerDraft:
        if not pack.decisions or not pack.evidence:
            return _insufficient(
                "I cannot find a defensible decision trail for this in the "
                "ingested repository history."
            )

        user = build_user_prompt(
            pack, strict=strict, max_answer_tokens=self.config.max_answer_tokens
        )
        schema = ANSWER_SCHEMA
        issue = ""
        for attempt in range(self.config.answer_attempts):
            try:
                raw = self.generator.complete(
                    system=ANSWER_SYSTEM_PROMPT,
                    user=user,
                    schema=schema,
                    schema_name=ANSWER_SCHEMA_NAME,
                )
            except (AppError, ValueError, TypeError) as error:
                issue = f"model call failed: {error}"
            else:
                draft = _parse_draft(raw)
                if isinstance(draft, AnswerDraft):
                    if (
                        draft.answer_status is not AnswerStatus.ANSWERED
                        or draft.citations
                    ):
                        return _normalize(draft)
                    issue = "answered without citations"
                else:
                    issue = draft
            if attempt + 1 < self.config.answer_attempts:
                user = build_user_prompt(
                    pack,
                    strict=True,
                    max_answer_tokens=self.config.max_answer_tokens,
                )
                user += f"\n\nVALIDATION ERROR: {issue}"
        return _insufficient(
            "I cannot produce a cited answer from the supplied evidence.",
            caveat=f"Answer validation failed: {issue or 'unknown error'}",
        )


def _parse_draft(raw: str) -> AnswerDraft | str:
    text = (raw or "").strip()
    if not text:
        return "empty response"
    text = _FENCE.sub("", text).strip()
    payload: Any = None
    for candidate in (text, _slice_object(text)):
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        break
    if not isinstance(payload, dict):
        return "response is not a JSON object"
    try:
        return AnswerDraft.model_validate(payload)
    except ValidationError as error:
        first = error.errors()[0]
        location = ".".join(str(part) for part in first.get("loc", ()))
        return f"schema error at {location or '<root>'}: {first.get('msg', 'invalid')}"


def _slice_object(text: str) -> str | None:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


def _normalize(draft: AnswerDraft) -> AnswerDraft:
    if draft.answer_status is AnswerStatus.INSUFFICIENT_EVIDENCE:
        return draft.model_copy(update={"confidence": AnswerConfidence.NONE})
    return draft


def _insufficient(message: str, *, caveat: str | None = None) -> AnswerDraft:
    caveats = [caveat] if caveat else []
    return AnswerDraft(
        answer_status=AnswerStatus.INSUFFICIENT_EVIDENCE,
        answer=message,
        citations=[],
        caveats=caveats,
        confidence=AnswerConfidence.NONE,
    )


__all__ = ["ANSWER_SCHEMA_NAME", "AnswerSynthesizer"]
