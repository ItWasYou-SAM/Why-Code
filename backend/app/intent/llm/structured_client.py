"""Structured-output client and schema gate.

The provider call is sandboxed: one system prompt, one user prompt, one JSON
schema. A syntactically valid response is still rejected if Pydantic finds
an unknown field, a missing evidence array, or an invalid target type.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.core.errors import AppError, InvalidRecordError
from app.intent.llm.provider import LLMProvider
from app.intent.llm.retry import call_with_retry

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


@dataclass
class StructuredResult:
    ok: bool
    schema_name: str
    data: dict[str, Any] | None = None
    raw: str | None = None
    issues: list[str] = field(default_factory=list)
    attempts: int = 0

    @property
    def error_text(self) -> str:
        return "; ".join(self.issues)


class StructuredClient:
    def __init__(
        self,
        provider: LLMProvider,
        *,
        max_attempts: int = 2,
        retry_base_delay: float = 0.5,
        on_retry: Any | None = None,
    ) -> None:
        self.provider = provider
        self.max_attempts = max(1, max_attempts)
        self.retry_base_delay = retry_base_delay
        self.on_retry = on_retry

    def extract(
        self,
        *,
        system: str,
        user: str,
        schema_model: type[BaseModel],
        schema_name: str,
    ) -> StructuredResult:
        schema = schema_model.model_json_schema()
        issues: list[str] = []
        raw: str | None = None
        attempts = 0
        corrective = ""
        prompt = user
        while attempts < self.max_attempts:
            attempts += 1
            snapshot = corrective

            def _call(corrective_text: str = snapshot) -> str:
                return self.provider.complete(
                    system=system,
                    user=prompt + corrective_text,
                    schema=schema,
                    schema_name=schema_name,
                )

            try:
                raw = call_with_retry(
                    _call,
                    attempts=self.max_attempts,
                    base_delay=self.retry_base_delay,
                    on_retry=self.on_retry,
                )
            except AppError as error:
                return StructuredResult(
                    ok=False,
                    schema_name=schema_name,
                    raw=raw,
                    issues=[f"provider_error: {error}"],
                    attempts=attempts,
                )

            parsed = parse_json_object(raw)
            if parsed is None:
                issues.append("response is not a JSON object")
                corrective = (
                    "\n\nCORRECTION: the previous response was not a JSON object. "
                    "Return only a JSON object matching the schema."
                )
                continue
            try:
                model = schema_model.model_validate(parsed)
            except ValidationError as error:
                issues.extend(_validation_issues(error))
                corrective = (
                    "\n\nCORRECTION: the previous response violated the schema. "
                    f"Problems: {'; '.join(issues[-5:])}. Return only corrected JSON."
                )
                continue
            return StructuredResult(
                ok=True,
                schema_name=schema_name,
                data=model.model_dump(mode="json"),
                raw=raw,
                attempts=attempts,
            )

        return StructuredResult(
            ok=False,
            schema_name=schema_name,
            raw=raw,
            issues=issues or ["structured extraction failed"],
            attempts=attempts,
        )


def parse_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    text = _FENCE.sub("", text).strip()
    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _validation_issues(error: ValidationError) -> list[str]:
    issues: list[str] = []
    for item in error.errors()[:10]:
        location = ".".join(str(part) for part in item.get("loc", ()))
        message = str(item.get("msg", "invalid value"))
        issues.append(f"schema: {location or '<root>'}: {message}")
    return issues


class SchemaValidator:
    """Re-validate a proposal separately from transport concerns."""

    def validate(
        self, schema_model: type[BaseModel], payload: dict[str, Any]
    ) -> tuple[BaseModel | None, list[str]]:
        try:
            return schema_model.model_validate(payload), []
        except ValidationError as error:
            return None, _validation_issues(error)

    def require(self, schema_model: type[BaseModel], payload: dict[str, Any]) -> BaseModel:
        model, issues = self.validate(schema_model, payload)
        if model is None:
            raise InvalidRecordError(
                f"invalid extraction payload: {'; '.join(issues)}",
                component="intent",
            )
        return model


__all__ = [
    "SchemaValidator",
    "StructuredClient",
    "StructuredResult",
    "parse_json_object",
]
