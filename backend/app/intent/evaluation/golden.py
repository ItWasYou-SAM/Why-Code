"""Loading and validation for the hand-curated golden decision set."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.errors import InvalidRecordError
from app.intent.evaluation.models import GoldenCase


def default_golden_path() -> Path:
    return Path(__file__).resolve().parent / "golden" / "decisions.jsonl"


def load_golden_cases(path: str | Path | None = None) -> list[GoldenCase]:
    """Load and validate a golden set from JSONL.

    A JSON array is also accepted so a reviewer can reformat the file without
    changing the evaluator. Every case must be parseable and the cross-case
    references (supersession target) must resolve.
    """

    golden_path = Path(path) if path is not None else default_golden_path()
    if not golden_path.exists():
        raise InvalidRecordError(
            f"no golden set at {golden_path}", component="intent.evaluation"
        )
    text = golden_path.read_text(encoding="utf-8")
    rows = _rows(text, golden_path)
    cases: list[GoldenCase] = []
    for number, row in enumerate(rows, start=1):
        try:
            cases.append(GoldenCase.model_validate(row))
        except ValidationError as error:
            raise InvalidRecordError(
                f"{golden_path}: case {number}: {_validation_message(error)}",
                component="intent.evaluation",
            ) from error
    return validate_golden_cases(cases, source=str(golden_path))


def validate_golden_cases(
    cases: list[GoldenCase], *, source: str = "<memory>"
) -> list[GoldenCase]:
    if not cases:
        raise InvalidRecordError(
            f"{source} contains no golden cases", component="intent.evaluation"
        )
    by_id: dict[str, GoldenCase] = {}
    for case in cases:
        if case.case_id in by_id:
            raise InvalidRecordError(
                f"{source}: duplicate case_id {case.case_id!r}",
                component="intent.evaluation",
            )
        by_id[case.case_id] = case
    for case in cases:
        expected = case.expected_supersession
        if expected is None:
            continue
        if expected.target_case_id == case.case_id:
            raise InvalidRecordError(
                f"{source}: {case.case_id!r} cannot supersede itself",
                component="intent.evaluation",
            )
        if expected.target_case_id not in by_id:
            raise InvalidRecordError(
                f"{source}: {case.case_id!r} supersedes unknown case "
                f"{expected.target_case_id!r}",
                component="intent.evaluation",
            )
        if by_id[expected.target_case_id].expected_status == "refused":
            raise InvalidRecordError(
                f"{source}: {case.case_id!r} cannot supersede a refused case",
                component="intent.evaluation",
            )
    return cases


def _rows(text: str, path: Path) -> list[dict[str, Any]]:
    stripped = text.lstrip()
    if stripped.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as error:
            raise InvalidRecordError(
                f"{path}: invalid JSON: {error.msg}", component="intent.evaluation"
            ) from error
        if not isinstance(parsed, list):
            raise InvalidRecordError(
                f"{path}: expected a JSON array of cases", component="intent.evaluation"
            )
        return [_object(row, path, number) for number, row in enumerate(parsed, 1)]

    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as error:
            raise InvalidRecordError(
                f"{path}:{number}: invalid JSON: {error.msg}",
                component="intent.evaluation",
            ) from error
        rows.append(_object(parsed, path, number))
    return rows


def _object(value: Any, path: Path, number: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidRecordError(
            f"{path}: case {number} is not a JSON object",
            component="intent.evaluation",
        )
    return value


def _validation_message(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item.get('loc', ())) or '<root>'}: "
        f"{item.get('msg', 'invalid value')}"
        for item in error.errors()[:8]
    )


__all__ = [
    "default_golden_path",
    "load_golden_cases",
    "validate_golden_cases",
]
