"""Ingestion CLI.

``python -m app.ingestion.cli ingest --repo encode/httpx --since ... --limit N``
wires settings, store, client, service and exporter into the artifacts the
plan's acceptance test names.

``python -m app.ingestion.cli show --repo encode/httpx --pr 1131`` prints
one PR's complete evidence trail (normalized records plus the raw payloads
they came from) without touching the network. ``build_client`` is a
module seam so tests inject a fake instead of hitting GitHub.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from app.core.config import Settings
from app.domain.evidence_ids import person_id
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

    if args.command == "show":
        return _show(args)
    if args.command not in {"ingest", "retry"}:
        parser.print_help()
        return 2
    if args.command == "ingest" and not _valid_date(args.since):
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
            since=getattr(args, "since", "1970-01-01"),
            limit=getattr(args, "limit", 100),
        )
        if args.command == "retry":
            result = service.retry_errored()
        else:
            result = service.run(force=args.force)
        export_normalized(store, artifacts / "normalized.jsonl")
        export_high_signal(store, artifacts / "high_signal_prs.jsonl", result.scored_payload())
        report = build_report(
            store,
            repo=args.repo,
            api_calls=result.api_calls,
            started_at=result.started_at,
            finished_at=result.finished_at,
            run_id=result.run_id,
            selected_prs=len(result.processed),
            processed_prs=len(result.completed),
            failed_prs=len(result.errored),
            high_signal_prs=result.high_signal_prs,
            rate_limit_remaining=result.rate_limit_remaining,
            failures=result.failures,
        )
        write_report(report, artifacts / "ingestion_report.json")
        print(
            f"{result.run_id}: {result.status} — "
            f"ingested {len(result.completed)} PRs, "
            f"{len(result.errored)} errored, {report['records_total']} records, "
            f"{result.api_calls} API requests"
        )
        return 0
    finally:
        store.close()
        close = getattr(client, "close", None)
        if callable(close):
            close()


def _show(args: argparse.Namespace) -> int:
    """Offline evidence-trail inspector: one PR, normalized + raw."""
    artifacts = (
        Path(args.artifacts_dir)
        if args.artifacts_dir
        else Path(os.environ.get("WHYCODE_ARTIFACTS_DIR", "artifacts"))
    )
    db_path = artifacts / "evidence.db"
    if not db_path.exists():
        print(f"no evidence store at {db_path}")
        return 1

    store = EvidenceStore(db_path)
    try:
        records = store.records_for_pr(args.repo, args.pr)
        # Person records are repo-scoped (deduped across PRs), so they are
        # not selected by pr_number. Reattach every author the PR cites.
        seen = {record.record_id for record in records}
        for login in sorted(
            {
                str(record.payload["author"])
                for record in records
                if record.payload.get("author")
            }
        ):
            person = store.get_record(person_id(repo=args.repo, login=login))
            if person is not None and person.record_id not in seen:
                records.append(person)
                seen.add(person.record_id)
        # Linked issues and doc blobs are real evidence for this PR even
        # though their raw keys are not namespaced by PR number.
        related_endpoints: set[str] = set()
        for record in records:
            if record.record_type.value == "issue" and record.payload.get("number"):
                related_endpoints.add(f"issues/{record.payload['number']}")
            if record.record_type.value == "doc_file" and record.payload.get("source_sha"):
                related_endpoints.add(f"git/blobs/{record.payload['source_sha']}")
        raw_ids = [
            source_id
            for source_id in store.raw_ids(prefix=f"{args.repo}:")
            if _belongs_to_pr(source_id.split(":", 1)[1], args.pr)
            or source_id.split(":", 1)[1] in related_endpoints
        ]
        if not records:
            print(f"no evidence for {args.repo}#{args.pr} (state: "
                  f"{store.get_pr_state(args.repo, args.pr)})")
            return 1

        if args.json:
            payload = {
                "repo": args.repo,
                "pr_number": args.pr,
                "state": store.get_pr_state(args.repo, args.pr),
                "error": store.get_pr_error(args.repo, args.pr),
                "records": [_record_row(record) for record in records],
                "raw": {
                    source_id: store.get_raw(source_id) for source_id in raw_ids
                },
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0

        print(f"{args.repo}#{args.pr} — state: {store.get_pr_state(args.repo, args.pr)}")
        error = store.get_pr_error(args.repo, args.pr)
        if error:
            print(f"error: {error}")
        for record in records:
            print(f"  {record.record_type.value:<15} {record.record_id}")
            summary = json.dumps(record.payload, sort_keys=True)
            print(f"    {summary[:300]}")
        print(f"  raw payloads ({len(raw_ids)}):")
        for source_id in raw_ids:
            print(f"    {source_id}")
        return 0
    finally:
        store.close()


def _record_row(record: Any) -> dict[str, Any]:
    return {
        "record_id": record.record_id,
        "record_type": record.record_type.value,
        "pr_number": record.pr_number,
        "content_hash": record.content_hash,
        "payload": record.payload,
    }


def _belongs_to_pr(endpoint: str, number: int) -> bool:
    parts = endpoint.split("/")
    return (
        len(parts) >= 2
        and parts[0] in {"pulls", "issues"}
        and parts[1] == str(number)
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.ingestion.cli")
    sub = parser.add_subparsers(dest="command")
    ingest = sub.add_parser("ingest", help="ingest merged PRs into the evidence store")
    ingest.add_argument("--repo", required=True)
    ingest.add_argument("--since", required=True, help="ISO date, e.g. 2024-01-01")
    ingest.add_argument("--limit", type=int, default=100)
    ingest.add_argument("--force", action="store_true")
    ingest.add_argument("--artifacts-dir", default=None)

    retry = sub.add_parser("retry", help="re-fetch only PRs currently marked error")
    retry.add_argument("--repo", required=True)
    retry.add_argument("--artifacts-dir", default=None)

    show = sub.add_parser("show", help="print one PR's evidence trail (offline)")
    show.add_argument("--repo", required=True)
    show.add_argument("--pr", type=int, required=True)
    show.add_argument("--artifacts-dir", default=None)
    show.add_argument("--json", action="store_true")
    return parser


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
