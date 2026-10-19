"""Domain records shared by ingestion, graph builder, and retrieval."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.record_types import RecordType


def content_hash(payload: dict[str, Any], record_type: str | RecordType | None = None) -> str:
    """Stable hash of a payload, optionally scoped to a record type.

    Keys are sorted and separators fixed so re-serialization never changes
    the hash. ``record_type`` is mixed in because the same payload under a
    different type is a different record.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    scoped = f"{record_type or ''}\x00{canonical}"
    return hashlib.sha256(scoped.encode("utf-8")).hexdigest()


class EvidenceRecord(BaseModel):
    """One normalized, replayable piece of evidence."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    record_type: RecordType
    repo: str = Field(min_length=1)
    pr_number: int | None = Field(default=None, ge=0)
    payload: dict[str, Any]
    content_hash: str = Field(min_length=1)
    ingested_at: datetime

    @field_validator("payload")
    @classmethod
    def _payload_present(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if value is None:
            raise ValueError("payload must be an object")
        return value
