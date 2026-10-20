"""Common extraction step.

Each extractor performs exactly one structured call. It returns proposals or
a rejection; it never validates evidence existence and never writes a graph
node. Those responsibilities belong to the validation and graph layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from app.intent.context.context_packer import EvidencePack
from app.intent.context.prompt_render import ExtractionKind, render_prompt
from app.intent.llm.schemas import SCHEMA_NAMES
from app.intent.llm.structured_client import StructuredClient


@dataclass
class ExtractionOutcome:
    kind: ExtractionKind
    ok: bool
    proposals: list[BaseModel] = field(default_factory=list)
    raw: str | None = None
    issues: list[str] = field(default_factory=list)
    attempts: int = 0
    missing_evidence: list[str] = field(default_factory=list)

    @property
    def error_text(self) -> str:
        return "; ".join(self.issues)


class BaseExtractor:
    kind: ExtractionKind
    schema_model: type[BaseModel]
    envelope_field: str

    def extract(
        self, pack: EvidencePack, client: StructuredClient
    ) -> ExtractionOutcome:
        system, user = render_prompt(self.kind, pack)
        schema_name = SCHEMA_NAMES[self.schema_model]
        result = client.extract(
            system=system,
            user=user,
            schema_model=self.schema_model,
            schema_name=schema_name,
        )
        if not result.ok or result.data is None:
            return ExtractionOutcome(
                kind=self.kind,
                ok=False,
                raw=result.raw,
                issues=result.issues,
                attempts=result.attempts,
            )
        raw_items = result.data.get(self.envelope_field, [])
        if not isinstance(raw_items, list):
            return ExtractionOutcome(
                kind=self.kind,
                ok=False,
                raw=result.raw,
                issues=[f"{self.envelope_field} must be a list"],
                attempts=result.attempts,
            )
        missing_evidence = result.data.get("missing_evidence", [])
        if not isinstance(missing_evidence, list) or not all(
            isinstance(item, str) for item in missing_evidence
        ):
            return ExtractionOutcome(
                kind=self.kind,
                ok=False,
                raw=result.raw,
                issues=["missing_evidence must be a list of strings"],
                attempts=result.attempts,
            )
        proposals: list[BaseModel] = []
        issues: list[str] = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                issues.append(f"{self.envelope_field}[{index}] is not an object")
                continue
            model, item_issues = _validate_item(self.schema_model, item, index)
            if model is None:
                issues.extend(item_issues)
                continue
            proposals.append(model)
        return ExtractionOutcome(
            kind=self.kind,
            ok=not issues,
            proposals=proposals,
            raw=result.raw,
            issues=issues,
            attempts=result.attempts,
            missing_evidence=missing_evidence,
        )


def _validate_item(
    schema_model: type[BaseModel], payload: dict[str, Any], index: int
) -> tuple[BaseModel | None, list[str]]:
    # ``schema_model`` describes the envelope. Validate one item against the
    # envelope's declared list item type, keeping validation errors local to
    # the offending proposal rather than rejecting the whole response.
    fields = getattr(schema_model, "model_fields", {})
    field_name = next(iter(fields), None)
    if field_name is None:
        return None, [f"schema {schema_model.__name__} has no fields"]
    annotation = fields[field_name].annotation
    item_model = _list_item_type(annotation)
    if item_model is None:
        return None, [f"schema {schema_model.__name__} is not an envelope"]
    try:
        return item_model.model_validate(payload), []
    except Exception as error:  # pydantic ValidationError
        issues: list[dict[str, Any]] = getattr(error, "errors", lambda: [])()
        messages: list[str] = []
        for issue in issues[:6]:
            location = ".".join(str(part) for part in issue.get("loc", ()))
            messages.append(
                f"{field_name}[{index}].{location}: {issue.get('msg', 'invalid')}"
            )
        return None, messages or [f"{field_name}[{index}]: invalid proposal"]


def _list_item_type(annotation: Any) -> type[BaseModel] | None:
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        args = getattr(annotation, "__args__", ())
        if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            return args[0]
    return None


__all__ = ["BaseExtractor", "ExtractionOutcome"]
