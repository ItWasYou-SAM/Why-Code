"""Test doubles for the structure suite: a content provider over the
fixture map (D-0015 seam), a logger spy that lets us prove D-0016, and a
changed-file record builder consistent with the C1/C2 corpus shape."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sources import SOURCES

REPO = "encode/httpx"
PR = 1131


def content_provider() -> Callable[[str, str | None], str]:
    def get(path: str, source_sha: str | None = None) -> str:
        if path not in SOURCES:
            raise KeyError(path)
        return SOURCES[path]

    return get


class LoggerSpy:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    @property
    def warnings(self) -> list[tuple[str, dict[str, Any]]]:
        return [e for e in self.events if e[0] == "warning"]

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append(("warning", {"event": event, **kw}))


def changed_file_record(path: str, pr_number: int = PR, sha: str = "s" * 40) -> dict[str, Any]:
    return {
        "record_id": f"httpx:pr:{pr_number}:file:{path}",
        "record_type": "changed_file",
        "repo": REPO,
        "pr_number": pr_number,
        "payload": {"path": path, "status": "modified", "source_sha": sha},
        "content_hash": "h" * 8,
        "ingested_at": "2026-10-19T10:00:00Z",
    }


def doc_file_record(path: str, content: str) -> dict[str, Any]:
    return {
        "record_id": f"httpx:doc:{path}",
        "record_type": "doc_file",
        "repo": REPO,
        "pr_number": None,
        "payload": {"path": path, "content": content},
        "content_hash": "d" * 8,
        "ingested_at": "2026-10-19T10:00:00Z",
    }


def corpus_records() -> list[dict[str, Any]]:
    """The changed-file set the enricher runs over: two python files,
    two config files, one env file, one binary, one broken file, one doc."""
    return [
        changed_file_record("httpx/_client.py"),
        changed_file_record("httpx/_config.py"),
        changed_file_record("httpx/__init__.py"),
        changed_file_record("broken/syntax.py"),
        changed_file_record("config/limits.toml"),
        changed_file_record("config/logging.yaml"),
        changed_file_record(".env.example"),
        changed_file_record("assets/logo.png", sha="b" * 40),
        doc_file_record("docs/api.md", "# API\nThe timeout env HTTPX_TIMEOUT controls requests.\n"),
        doc_file_record("docs/other.md", "# Other\nNothing relevant.\n"),
    ]
