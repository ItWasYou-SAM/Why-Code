"""Artifact exports: normalized JSONL, high-signal PR list, run report.

Exports are deterministic (sorted by record_id) so a rerun of the same
corpus is byte-identical; the CLI idempotency contract depends on it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.record_types import RecordType
from app.ingestion.store import EvidenceStore


def export_normalized(store: EvidenceStore, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(_record_line(record), sort_keys=True)
        for record in store.iter_records()
    ]
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return out


def export_high_signal(
    store: EvidenceStore, path: str | Path, scores: dict[int, dict[str, Any]]
) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"pr_number": number, **scores[number]}
        for number in sorted(scores)
        if str(scores[number].get("band")) == "high"
    ]
    out.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )
    return out


def build_report(
    store: EvidenceStore, *, repo: str, api_calls: int, started_at: str
) -> dict[str, Any]:
    states = store.pr_status_counts()
    return {
        "repo": repo,
        "started_at": started_at,
        "api_calls": api_calls,
        "prs_total": sum(states.values()),
        "prs_complete": states.get("complete", 0),
        "prs_errored": states.get("error", 0),
        "prs_pending": states.get("pending", 0),
        "records_total": store.count_records(),
        "by_type": store.counts_by_type(),
    }


def write_report(report: dict[str, Any], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def _record_line(record: Any) -> dict[str, Any]:
    ingested = record.ingested_at
    return {
        "record_id": record.record_id,
        "record_type": str(record.record_type),
        "repo": record.repo,
        "pr_number": record.pr_number,
        "payload": record.payload,
        "content_hash": record.content_hash,
        "ingested_at": ingested if isinstance(ingested, str) else ingested.isoformat(),
    }


__all__ = [
    "RecordType",
    "build_report",
    "export_high_signal",
    "export_normalized",
    "write_report",
]
