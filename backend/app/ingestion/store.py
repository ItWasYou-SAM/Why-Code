"""EvidenceStore: idempotent SQLite persistence for Component 1.

Normalized records and raw payloads live in separate tables: the raw
archive is the replay source of truth, the normalized table is the curated
view. Per-PR writes are buffered and committed in one transaction so a
failure rolls back the whole PR (D-0007).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from app.core.errors import InvalidRecordError
from app.domain.record_types import RecordType
from app.domain.records import EvidenceRecord

VALID_PR_STATUSES = ("pending", "complete", "error")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence_records (
    record_id    TEXT PRIMARY KEY,
    record_type  TEXT NOT NULL,
    repo         TEXT NOT NULL,
    pr_number    INTEGER,
    payload      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    ingested_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_pr ON evidence_records(repo, pr_number);
CREATE INDEX IF NOT EXISTS idx_evidence_type ON evidence_records(record_type);

CREATE TABLE IF NOT EXISTS raw_payloads (
    source_id TEXT PRIMARY KEY,
    endpoint  TEXT NOT NULL,
    payload   TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_state (
    repo         TEXT NOT NULL,
    pr_number    INTEGER NOT NULL,
    status       TEXT NOT NULL,
    completed_at TEXT,
    error        TEXT,
    PRIMARY KEY (repo, pr_number)
);
"""


class EvidenceStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        self._buffer: list[EvidenceRecord] = []
        self._buffer_pr: tuple[str, int] | None = None

    def close(self) -> None:
        self.conn.close()

    # -- normalized records --------------------------------------------
    def upsert_record(self, record: EvidenceRecord) -> None:
        self._write_records([record])

    def _write_records(self, records: list[EvidenceRecord]) -> None:
        self.conn.executemany(
            """
            INSERT INTO evidence_records
                (record_id, record_type, repo, pr_number, payload,
                 content_hash, ingested_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                record_type=excluded.record_type,
                repo=excluded.repo,
                pr_number=excluded.pr_number,
                payload=excluded.payload,
                content_hash=excluded.content_hash,
                ingested_at=excluded.ingested_at,
                updated_at=excluded.updated_at
            """,
            [
                (
                    r.record_id,
                    str(r.record_type),
                    r.repo,
                    r.pr_number,
                    json.dumps(r.payload, sort_keys=True),
                    r.content_hash,
                    _as_text(r.ingested_at),
                    _as_text(r.ingested_at),
                )
                for r in records
            ],
        )
        self.conn.commit()

    def get_record(self, record_id: str) -> EvidenceRecord | None:
        row = self.conn.execute(
            "SELECT * FROM evidence_records WHERE record_id = ?", (record_id,)
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def iter_records(self, record_type: RecordType | None = None) -> Iterator[EvidenceRecord]:
        if record_type is None:
            rows = self.conn.execute(
                "SELECT * FROM evidence_records ORDER BY record_id"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM evidence_records WHERE record_type = ? ORDER BY record_id",
                (str(record_type),),
            ).fetchall()
        for row in rows:
            yield _row_to_record(row)

    def count_records(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM evidence_records").fetchone()
        return int(row["c"])

    def counts_by_type(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT record_type, COUNT(*) AS c FROM evidence_records GROUP BY record_type"
        ).fetchall()
        return {row["record_type"]: int(row["c"]) for row in rows}

    # -- raw payloads ---------------------------------------------------
    def upsert_raw(self, source_id: str, endpoint: str, payload: dict[str, Any]) -> None:
        from datetime import UTC, datetime

        self.conn.execute(
            """
            INSERT INTO raw_payloads (source_id, endpoint, payload, fetched_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                endpoint=excluded.endpoint,
                payload=excluded.payload,
                fetched_at=excluded.fetched_at
            """,
            (
                source_id,
                endpoint,
                json.dumps(payload, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()

    def get_raw(self, source_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT payload FROM raw_payloads WHERE source_id = ?", (source_id,)
        ).fetchone()
        return json.loads(row["payload"]) if row is not None else None

    def raw_ids(self) -> list[str]:
        rows = self.conn.execute("SELECT source_id FROM raw_payloads ORDER BY source_id")
        return [row["source_id"] for row in rows]

    # -- ingestion state ------------------------------------------------
    def set_pr_state(
        self, repo: str, pr_number: int, status: str, error: str | None = None
    ) -> None:
        if status not in VALID_PR_STATUSES:
            raise InvalidRecordError(
                f"unknown PR status {status!r}", component="ingestion",
                record_id=f"{repo}:pr:{pr_number}",
            )
        from datetime import UTC, datetime

        completed_at = datetime.now(UTC).isoformat() if status == "complete" else None
        self.conn.execute(
            """
            INSERT INTO ingestion_state (repo, pr_number, status, completed_at, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(repo, pr_number) DO UPDATE SET
                status=excluded.status,
                completed_at=excluded.completed_at,
                error=excluded.error
            """,
            (repo, pr_number, status, completed_at, error),
        )
        self.conn.commit()

    def get_pr_state(self, repo: str, pr_number: int) -> str:
        row = self.conn.execute(
            "SELECT status FROM ingestion_state WHERE repo = ? AND pr_number = ?",
            (repo, pr_number),
        ).fetchone()
        return str(row["status"]) if row is not None else "pending"

    def get_pr_error(self, repo: str, pr_number: int) -> str | None:
        row = self.conn.execute(
            "SELECT error FROM ingestion_state WHERE repo = ? AND pr_number = ?",
            (repo, pr_number),
        ).fetchone()
        return row["error"] if row is not None else None

    def pr_numbers_with_status(self, status: str) -> list[int]:
        rows = self.conn.execute(
            "SELECT pr_number FROM ingestion_state WHERE status = ? ORDER BY pr_number",
            (status,),
        ).fetchall()
        return [int(row["pr_number"]) for row in rows]

    def pr_status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS c FROM ingestion_state GROUP BY status"
        ).fetchall()
        return {row["status"]: int(row["c"]) for row in rows}

    # -- per-PR transaction ---------------------------------------------
    def begin_pr(self, repo: str, pr_number: int) -> None:
        if self._buffer_pr is not None:
            raise InvalidRecordError(
                "another PR transaction is already open", component="ingestion",
                record_id=f"{repo}:pr:{pr_number}",
            )
        self._buffer = []
        self._buffer_pr = (repo, pr_number)

    def buffer_record(self, record: EvidenceRecord) -> None:
        if self._buffer_pr is None:
            raise InvalidRecordError(
                "buffer_record called outside a PR transaction",
                component="ingestion",
                record_id=record.record_id,
            )
        self._buffer.append(record)

    def commit_pr(self, repo: str, pr_number: int) -> None:
        if self._buffer_pr != (repo, pr_number):
            raise InvalidRecordError(
                "commit_pr does not match the open transaction",
                component="ingestion",
                record_id=f"{repo}:pr:{pr_number}",
            )
        self._write_records(self._buffer)
        self._buffer = []
        self._buffer_pr = None
        self.set_pr_state(repo, pr_number, "complete")

    def rollback_pr(self, repo: str, pr_number: int, error: str | None = None) -> None:
        self._buffer = []
        self._buffer_pr = None
        self.set_pr_state(repo, pr_number, "error", error=error or "rolled back")


def _as_text(value: Any) -> str:
    return value if isinstance(value, str) else value.isoformat()


def _row_to_record(row: sqlite3.Row) -> EvidenceRecord:
    return EvidenceRecord(
        record_id=row["record_id"],
        record_type=RecordType(row["record_type"]),
        repo=row["repo"],
        pr_number=row["pr_number"],
        payload=json.loads(row["payload"]),
        content_hash=row["content_hash"],
        ingested_at=row["ingested_at"],
    )
