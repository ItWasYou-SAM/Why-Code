"""Ingestion CLI.

`python -m app.ingestion.cli ingest --repo encode/httpx --since ... --limit N`
wires settings, store, client, service and exporter into the four
artifacts the plan's acceptance test names. ``build_client`` is a module
seam so tests inject a fake instead of hitting the network.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.ingestion.exporter import (
    build_report,
    export_high_signal,
    export_normalized,
    write_report,
)
from app.ingestion.github_client import GitHubRestClient
from app.ingestion.service import IngestionService
from app.ingestion.store import EvidenceStore


def build_client(token: str) -> GitHubRestClient:
    return GitHubRestClient(github_token=token)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)

    if args.command != "ingest":
        parser.print_help()
        return 2
    if not _valid_date(args.since):
        print(f"invalid --since date: {args.since!r} (expected YYYY-MM-DD)")
        return 2

    settings = Settings.from_env()
    artifacts = Path(args.artifacts_dir) if args.artifacts_dir else settings.artifacts_dir
    artifacts.mkdir(parents=True, exist_ok=True)

    store = EvidenceStore(artifacts / "evidence.db")
    client = build_client(settings.github_token)
    try:
        service = IngestionService(
            github=client,
            store=store,
            repo=args.repo,
            since=args.since,
            limit=args.limit,
        )
        result = service.run(force=args.force)
        export_normalized(store, artifacts / "normalized.jsonl")
        export_high_signal(store, artifacts / "high_signal_prs.jsonl", result.scored_payload())
        report = build_report(
            store, repo=args.repo, api_calls=result.api_calls, started_at=result.started_at
        )
        write_report(report, artifacts / "ingestion_report.json")
        print(
            f"ingested {len(result.completed)} PRs, "
            f"{len(result.errored)} errored, {report['records_total']} records"
        )
        return 0
    finally:
        store.close()
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.ingestion.cli")
    sub = parser.add_subparsers(dest="command")
    ingest = sub.add_parser("ingest", help="ingest merged PRs into the evidence store")
    ingest.add_argument("--repo", required=True)
    ingest.add_argument("--since", required=True, help="ISO date, e.g. 2024-01-01")
    ingest.add_argument("--limit", type=int, default=100)
    ingest.add_argument("--force", action="store_true")
    ingest.add_argument("--artifacts-dir", default=None)
    return parser


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _unused(*_: Any) -> None:  # pragma: no cover - keeps import surface explicit
    return None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
