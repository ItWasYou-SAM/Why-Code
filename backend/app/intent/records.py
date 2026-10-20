"""Small adapters over normalized records.

Components 1 and 2 pass plain dictionaries; tests and in-process callers may
pass :class:`EvidenceRecord`. The intent pipeline should not care which, so
every access goes through these helpers and every value is copied into a
plain payload.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from app.core.errors import InvalidRecordError
from app.domain.records import EvidenceRecord


def payload_of(record: EvidenceRecord | dict[str, Any]) -> dict[str, Any]:
    if isinstance(record, EvidenceRecord):
        return dict(record.payload)
    payload = record.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def record_id_of(record: EvidenceRecord | dict[str, Any]) -> str:
    if isinstance(record, EvidenceRecord):
        return record.record_id
    value = record.get("record_id")
    if not isinstance(value, str) or not value.strip():
        raise InvalidRecordError(
            "normalized record has no record_id", component="intent"
        )
    return value


def record_type_of(record: EvidenceRecord | dict[str, Any]) -> str:
    value = (
        record.record_type
        if isinstance(record, EvidenceRecord)
        else record.get("record_type")
    )
    if value is None:
        return ""
    return value.value if hasattr(value, "value") else str(value)


def pr_number_of(record: EvidenceRecord | dict[str, Any]) -> int | None:
    value = record.pr_number if isinstance(record, EvidenceRecord) else record.get(
        "pr_number"
    )
    return value if isinstance(value, int) else None


def content_hash_of(record: EvidenceRecord | dict[str, Any]) -> str | None:
    value = record.content_hash if isinstance(record, EvidenceRecord) else record.get(
        "content_hash"
    )
    return value if isinstance(value, str) and value else None


def as_dict(record: EvidenceRecord | dict[str, Any]) -> dict[str, Any]:
    if isinstance(record, dict):
        return dict(record)
    return {
        "record_id": record.record_id,
        "record_type": record.record_type.value,
        "repo": record.repo,
        "pr_number": record.pr_number,
        "payload": dict(record.payload),
        "content_hash": record.content_hash,
        "ingested_at": record.ingested_at.isoformat(),
    }


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    text = Path(path).read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise InvalidRecordError(
                f"invalid JSON on line {number}: {error.msg}",
                component="intent",
            ) from error
        if not isinstance(parsed, dict):
            raise InvalidRecordError(
                f"line {number} is not a JSON object", component="intent"
            )
        rows.append(parsed)
    return rows


def text_of(record: EvidenceRecord | dict[str, Any]) -> str:
    payload = payload_of(record)
    for key in ("body", "content", "message", "title"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def author_of(record: EvidenceRecord | dict[str, Any]) -> str | None:
    value = payload_of(record).get("author")
    return value if isinstance(value, str) and value.strip() else None


def url_of(record: EvidenceRecord | dict[str, Any]) -> str | None:
    value = payload_of(record).get("url")
    return value if isinstance(value, str) and value.strip() else None


def flattened_text(records: Iterable[EvidenceRecord | dict[str, Any]]) -> str:
    return "\n".join(text_of(record) for record in records if text_of(record))


__all__ = [
    "as_dict",
    "author_of",
    "content_hash_of",
    "flattened_text",
    "load_jsonl",
    "payload_of",
    "pr_number_of",
    "record_id_of",
    "record_type_of",
    "text_of",
    "url_of",
]
