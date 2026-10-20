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
    store: EvidenceStore,
    *,
    repo: str,
    api_calls: int,
    started_at: str,
    finished_at: str = "",
    run_id: str = "",
    selected_prs: int | None = None,
    processed_prs: int | None = None,
    failed_prs: int | None = None,
    high_signal_prs: int = 0,
    rate_limit_remaining: int | None = None,
    failures: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the run report from the run's result plus DB counts.

    Counts of *this run* come from the caller (a DB-wide count would mix
    runs). Record counts come from the database, which is what makes the
    report auditable: every number can be re-queried.
    """
    states = store.pr_status_counts()
    processed = states.get("complete", 0) if processed_prs is None else processed_prs
    failed = states.get("error", 0) if failed_prs is None else failed_prs
    selected = processed + failed if selected_prs is None else selected_prs
    failure_rows = list(failures or [])

    if failed and processed:
        status = "completed_with_failures"
    elif failed:
        status = "failed"
    else:
        status = "completed"

    by_type = {record_type.value: 0 for record_type in RecordType}
    by_type.update(store.counts_by_type())

    return {
        "run_id": run_id,
        "repo": repo,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "selected_prs": selected,
        "processed_prs": processed,
        "failed_prs": failed,
        "records": by_type,
        "records_total": store.count_records(),
        "by_type": dict(by_type),
        "high_signal_prs": high_signal_prs,
        "api_requests": api_calls,
        "api_calls": api_calls,
        "rate_limit_remaining": rate_limit_remaining,
        "failures": failure_rows,
        # Legacy keys kept because earlier callers/tests use them.
        "prs_total": sum(states.values()),
        "prs_complete": states.get("complete", 0),
        "prs_errored": states.get("error", 0),
        "prs_pending": states.get("pending", 0),
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
