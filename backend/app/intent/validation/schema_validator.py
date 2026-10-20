"""Proposal schema validation.

The transport layer already validates against the provider schema. This layer
adds cross-field rules that are easier to express in code than in JSON Schema:
atomic claims, unique evidence, unique targets, participant evidence that is
actually cited, and no intent identifiers masquerading as evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from app.intent.llm.schemas import CandidateExtraction, ExtractedCandidate


@dataclass
class SchemaValidation:
    ok: bool
    model: BaseModel | None = None
    issues: list[str] = field(default_factory=list)


class SchemaValidator:
    def validate(
        self, schema_model: type[BaseModel], payload: dict[str, Any]
    ) -> SchemaValidation:
        try:
            model = schema_model.model_validate(payload)
        except ValidationError as error:
            return SchemaValidation(ok=False, issues=_issues(error))
        issues = self._cross_field(model)
        return SchemaValidation(ok=not issues, model=model, issues=issues)

    def validate_proposal(self, payload: dict[str, Any]) -> SchemaValidation:
        return self.validate(ExtractedCandidate, payload)

    def _cross_field(self, model: BaseModel) -> list[str]:
        if isinstance(model, CandidateExtraction):
            issues: list[str] = []
            for index, candidate in enumerate(model.candidates):
                issues.extend(
                    f"candidates[{index}]: {issue}"
                    for issue in self._cross_field(candidate)
                )
            return issues
        if not isinstance(model, ExtractedCandidate):
            return []

        issues = []
        if model.rationale.strip().casefold() == model.claim.strip().casefold():
            issues.append(
                "rationale must add information beyond restating the claim"
            )
        for evidence_id in model.evidence_ids:
            lowered = evidence_id.lower()
            if lowered.startswith(("dec_", "con_", "rev_", "inc_", "evi_")):
                issues.append(
                    f"evidence id {evidence_id!r} names an intent/evidence "
                    "wrapper, not a source record"
                )
        cited = set(model.evidence_ids)
        for participant in model.participants:
            outside = set(participant.evidence_ids) - cited
            if outside:
                issues.append(
                    f"participant {participant.login!r} cites evidence not in "
                    f"the candidate: {', '.join(sorted(outside))}"
                )
        return issues


def _issues(error: ValidationError) -> list[str]:
    messages: list[str] = []
    for item in error.errors()[:10]:
        location = ".".join(str(part) for part in item.get("loc", ()))
        messages.append(f"{location or '<root>'}: {item.get('msg', 'invalid')}")
    return messages


__all__ = ["SchemaValidation", "SchemaValidator"]
