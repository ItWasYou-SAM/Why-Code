"""Shared structural test helpers: fixture paths and a fake Neo4j session."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO = "encode/httpx"
SHORT = "httpx"
COMMIT = "a" * 40
COMMIT_B = "b" * 40


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def record(
    record_id: str,
    record_type: str,
    payload: dict[str, Any],
    pr_number: int | None = None,
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "record_type": record_type,
        "repo": REPO,
        "pr_number": pr_number,
        "payload": payload,
        "content_hash": "deadbeef",
        "ingested_at": "2026-10-19T10:00:00Z",
    }


def corpus(
    *,
    pr_number: int = 1131,
    commit_sha: str = COMMIT,
    paths: tuple[str, ...] = (
        "httpx/_client.py",
        "httpx/_config.py",
        "httpx/_transport.py",
        "docs/retries.md",
        "pyproject.toml",
    ),
    doc_contents: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    records = [
        record(
            f"{SHORT}:pr:{pr_number}",
            "pull_request",
            {
                "number": pr_number,
                "title": "Restrict retries to connection errors",
                "author": "tomchristie",
                "merged": True,
                "merged_at": "2024-04-02T06:30:45Z",
                "url": f"https://github.com/{REPO}/pull/{pr_number}",
                "body": "",
            },
            pr_number,
        ),
        record(
            f"{SHORT}:commit:{commit_sha}",
            "commit",
            {
                "sha": commit_sha,
                "message": "Restrict retries",
                "author": "tomchristie",
                "committed_at": "2024-03-31T10:00:00Z",
            },
            pr_number,
        ),
    ]
    for path in paths:
        records.append(
            record(
                f"{SHORT}:pr:{pr_number}:file:{path}",
                "changed_file",
                {
                    "path": path,
                    "status": "modified",
                    "additions": 1,
                    "deletions": 0,
                    "patch": "@@ -1 +1 @@",
                },
                pr_number,
            )
        )
    contents = doc_contents or {}
    for path, content in contents.items():
        records.append(
            record(
                f"{SHORT}:doc:{path}",
                "doc_file",
                {"path": path, "content": content, "source_sha": COMMIT},
                pr_number,
            )
        )
    return records


def high_signal(pr_number: int = 1131) -> list[dict[str, Any]]:
    return [{"pr_number": pr_number, "score": 0.8, "band": "high"}]


class FakeSession:
    """Records Cypher calls; rejects writes when blocked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.blocked = False

    def run(self, query: str, **parameters: Any) -> None:
        if self.blocked:
            raise AssertionError(f"unexpected write: {query}")
        self.calls.append((query, parameters))

    @property
    def executed(self) -> int:
        return len(self.calls)

    @property
    def queries(self) -> list[str]:
        return [query for query, _ in self.calls]

    def write_jsonl(self, path: Path, records: list[dict[str, Any]]) -> Path:
        path.write_text(
            "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
        )
        return path
