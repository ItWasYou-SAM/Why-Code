"""Prediction loading for the evaluation harness.

The canonical input is ``artifacts/intent/intent_batch.json``, but a reviewer
can also score a JSONL file of intent records or a small hand-written JSON
file. The loader never invents graph keys: it reads the IDs the run produced
and derives a PR number from an evidence ID only when the intent omitted one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.errors import InvalidRecordError
from app.intent.evaluation.models import (
    PredictedDecision,
    PredictedEdge,
    PredictedTarget,
    PredictionSet,
    normalize_golden_target,
)


def load_predictions(path: str | Path) -> PredictionSet:
    prediction_path = Path(path)
    if not prediction_path.exists():
        raise InvalidRecordError(
            f"no predictions at {prediction_path}", component="intent.evaluation"
        )
    text = prediction_path.read_text(encoding="utf-8")
    try:
        return predictions_from_payload(_payload(text, prediction_path), prediction_path)
    except ValidationError as error:
        raise InvalidRecordError(
            f"{prediction_path}: {_validation_message(error)}",
            component="intent.evaluation",
        ) from error


def predictions_from_payload(
    payload: Any, source: str | Path = "<memory>"
) -> PredictionSet:
    """Convert an intent-batch-shaped payload into a prediction set."""

    if isinstance(payload, list):
        return _from_container({"intents": payload}, source)
    if isinstance(payload, dict):
        return _from_container(payload, source)
    raise InvalidRecordError(
        f"{source}: predictions must be a JSON object or array",
        component="intent.evaluation",
    )


def _payload(text: str, path: Path) -> Any:
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise InvalidRecordError(
                f"{path}: invalid JSON: {error.msg}", component="intent.evaluation"
            ) from error
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise InvalidRecordError(
                f"{path}:{number}: invalid JSON: {error.msg}",
                component="intent.evaluation",
            ) from error
        if not isinstance(row, dict):
            raise InvalidRecordError(
                f"{path}:{number}: intent record is not an object",
                component="intent.evaluation",
            )
        rows.append(row)
    return rows


def _from_container(payload: dict[str, Any], source: str | Path) -> PredictionSet:
    raw_intents = payload.get("intents", payload.get("decisions"))
    if raw_intents is None:
        raise InvalidRecordError(
            f"{source}: expected an 'intents' or 'decisions' list",
            component="intent.evaluation",
        )
    if not isinstance(raw_intents, list):
        raise InvalidRecordError(
            f"{source}: intents must be a list", component="intent.evaluation"
        )

    decisions: list[PredictedDecision] = []
    for index, raw in enumerate(raw_intents, start=1):
        if not isinstance(raw, dict):
            raise InvalidRecordError(
                f"{source}: intent {index} is not an object",
                component="intent.evaluation",
            )
        decision = _decision(raw, index)
        if decision is not None:
            decisions.append(decision)

    edges = _edges(payload)
    refusals, provided = _refusals(payload)
    return PredictionSet(
        decisions=decisions,
        edges=edges,
        refusals=refusals,
        refusals_provided=provided,
    )


def _decision(raw: dict[str, Any], index: int) -> PredictedDecision | None:
    kind = str(raw.get("kind", "decision")).strip().lower()
    if kind == "incident":
        return None
    evidence_ids = _string_list(raw.get("evidence_ids"))
    pr_number = _int_or_none(raw.get("pr_number"))
    if pr_number is None:
        pr_number = _pr_from_evidence(evidence_ids)
    identifier = raw.get("id", raw.get("decision_id"))
    if not isinstance(identifier, str) or not identifier.strip():
        identifier = f"prediction_{index}"
    claim = raw.get("claim", raw.get("summary"))
    if not isinstance(claim, str) or not claim.strip():
        raise InvalidRecordError(
            f"prediction {identifier!r} has no claim", component="intent.evaluation"
        )
    return PredictedDecision(
        id=identifier.strip(),
        pr_number=pr_number,
        claim=" ".join(claim.split()),
        kind=kind,
        status=str(raw.get("status", "active")),
        evidence_ids=evidence_ids,
        targets=_targets(raw.get("targets")),
    )


def _targets(raw: Any) -> list[PredictedTarget]:
    if not isinstance(raw, list):
        return []
    targets: list[PredictedTarget] = []
    for item in raw:
        if isinstance(item, str):
            try:
                parsed = normalize_golden_target(item)
            except ValueError as error:
                raise InvalidRecordError(
                    str(error), component="intent.evaluation"
                ) from error
            kind, _, identifier = parsed.partition(":")
            targets.append(PredictedTarget(type=kind, identifier=identifier))
            continue
        if not isinstance(item, dict):
            continue
        target_type = item.get("type", item.get("target_type"))
        target_identifier = item.get("identifier", item.get("target_id"))
        if not isinstance(target_type, str) or not isinstance(
            target_identifier, str
        ):
            continue
        targets.append(
            PredictedTarget(type=target_type, identifier=target_identifier)
        )
    return targets


def _edges(payload: dict[str, Any]) -> list[PredictedEdge]:
    edges: list[PredictedEdge] = []
    for item in _dict_list(payload.get("supersession_edges")):
        rel_type = _text(item.get("rel_type", item.get("relationship")))
        start = _text(item.get("start_id", item.get("later_decision_id")))
        end = _text(item.get("end_id", item.get("earlier_decision_id")))
        if rel_type is not None and start is not None and end is not None:
            edges.append(
                PredictedEdge(rel_type=rel_type, start_id=start, end_id=end)
            )
    for item in _dict_list(payload.get("supersession_decisions")):
        relationship = _text(item.get("relationship"))
        start = _text(item.get("later_decision_id"))
        end = _text(item.get("earlier_decision_id"))
        if relationship is not None and start is not None and end is not None:
            edges.append(
                PredictedEdge(rel_type=relationship, start_id=start, end_id=end)
            )
    for item in _dict_list(payload.get("edges")):
        rel_type = _text(item.get("rel_type", item.get("relationship")))
        start = _text(item.get("start_id", item.get("later_decision_id")))
        end = _text(item.get("end_id", item.get("earlier_decision_id")))
        if rel_type is not None and start is not None and end is not None:
            edges.append(
                PredictedEdge(rel_type=rel_type, start_id=start, end_id=end)
            )
    return _deduplicate_edges(edges)


def _deduplicate_edges(edges: list[PredictedEdge]) -> list[PredictedEdge]:
    seen: set[tuple[str, str, str]] = set()
    unique: list[PredictedEdge] = []
    for edge in edges:
        key = (edge.rel_type, edge.start_id, edge.end_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique


def _refusals(payload: dict[str, Any]) -> tuple[set[int], bool]:
    raw = payload.get("refusals")
    if raw is None:
        return set(), False
    if not isinstance(raw, list):
        return set(), False
    numbers: set[int] = set()
    for item in raw:
        if isinstance(item, int) and not isinstance(item, bool):
            numbers.add(item)
        elif isinstance(item, dict):
            number = _int_or_none(item.get("pr_number"))
            if number is not None:
                numbers.add(number)
    return numbers, True


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [
        item.strip()
        for item in value
        if isinstance(item, str) and item.strip()
    ]


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _pr_from_evidence(evidence_ids: list[str]) -> int | None:
    for evidence_id in evidence_ids:
        parts = evidence_id.split(":")
        if len(parts) >= 3 and parts[1] == "pr":
            try:
                return int(parts[2])
            except ValueError:
                continue
    return None


def _validation_message(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item.get('loc', ())) or '<root>'}: "
        f"{item.get('msg', 'invalid value')}"
        for item in error.errors()[:8]
    )


__all__ = ["load_predictions", "predictions_from_payload"]
