"""Helpers shared by the intent tests.

The helpers build normalized records, a fake evidence store, a graph index
with one symbol/config key/file, and a static structured client. They keep
each test focused on one contract instead of on fixture plumbing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.domain.records import EvidenceRecord
from app.graph.records import GraphNode, GraphRecords
from app.intent.llm.structured_client import StructuredClient

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REPO = "encode/httpx"
SHORT = "httpx"
COMMIT = "a" * 40
SYMBOL_KEY = f"{SHORT}:symbol:httpx/_client.py@{COMMIT}:Client._send_handling_redirects"
QUALIFIED = "httpx._client.Client._send_handling_redirects"
CONFIG_KEY = f"{SHORT}:config:httpx/_config.py:retries"
FILE_KEY = f"{SHORT}:file:httpx/_config.py@{COMMIT}"


def fixture(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def records(name: str) -> list[EvidenceRecord]:
    return [EvidenceRecord.model_validate(row) for row in fixture(name)]


class FakeStore:
    """The smallest possible EvidenceLookup implementation."""

    def __init__(self, records: list[Any]) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        for record in records:
            model = (
                record
                if isinstance(record, EvidenceRecord)
                else EvidenceRecord.model_validate(record)
            )
            self._records[model.record_id] = model

    def get_record(self, record_id: str) -> EvidenceRecord | None:
        return self._records.get(record_id)


def structural_records() -> GraphRecords:
    nodes = [
        GraphNode(
            label="File",
            key=FILE_KEY,
            properties={
                "path": "httpx/_config.py",
                "commit_sha": COMMIT,
                "record_id": FILE_KEY,
                "repo": REPO,
            },
        ),
        GraphNode(
            label="Symbol",
            key=SYMBOL_KEY,
            properties={
                "name": "_send_handling_redirects",
                "qualname": "Client._send_handling_redirects",
                "qualified_name": QUALIFIED,
                "kind": "method",
                "path": "httpx/_client.py",
                "commit_sha": COMMIT,
                "start_line": 100,
                "end_line": 140,
                "record_id": SYMBOL_KEY,
                "repo": REPO,
            },
        ),
        GraphNode(
            label="ConfigKey",
            key=CONFIG_KEY,
            properties={
                "key": "retries",
                "source_path": "httpx/_config.py",
                "source_kind": "yaml",
                "line": 3,
                "record_id": CONFIG_KEY,
                "repo": REPO,
            },
        ),
    ]
    return GraphRecords(nodes=nodes, relationships=[])


def static_client(responses: dict[str, str | list[str]]) -> StructuredClient:
    from app.intent.llm.provider import StaticProvider

    return StructuredClient(StaticProvider(responses), max_attempts=1)


def evidence_item_types(name: str) -> list[str]:
    return [row["record_type"] for row in fixture(name)]
