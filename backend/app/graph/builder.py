"""Builder orchestration: schema, load, dry-run, rebuild.

The builder is the only graph module that performs I/O. It builds a full
plan, validates it, and only then emits statements — so an integrity error
means zero database operations (D-0011), and dry-run differs from a real
load by exactly one boolean.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from app.core.errors import GraphWriteError, InvalidRecordError
from app.graph.batch_writer import DEFAULT_BATCH_SIZE, write_records
from app.graph.neo4j_adapter import (
    delete_nodes,
    schema_statements,
)
from app.graph.ops import LABEL_BY_TYPE, GraphPlan


class SessionLike(Protocol):
    def run(self, query: str, **parameters: Any) -> Any: ...


@dataclass
class BuildReport:
    nodes: dict[str, int] = field(default_factory=dict)
    rels: dict[str, int] = field(default_factory=dict)
    dry_run: bool = False
    repo: str = ""

    def counts_shape(self) -> dict[str, dict[str, int]]:
        return {"nodes": dict(self.nodes), "rels": dict(self.rels)}

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "dry_run": self.dry_run,
            "nodes": dict(self.nodes),
            "rels": dict(self.rels),
        }


class GraphBuilder:
    def __init__(self, session: SessionLike, batch_size: int = DEFAULT_BATCH_SIZE) -> None:
        self.session = session
        self.batch_size = batch_size

    # -- schema ---------------------------------------------------------
    def prepare_schema(self) -> None:
        labels = list(dict.fromkeys(LABEL_BY_TYPE.values()))
        labels.extend(["Repo", "CodeRegion"])
        for statement in schema_statements(labels):
            self.session.run(statement)

    # -- loading --------------------------------------------------------
    def load_jsonl_text(
        self,
        text: str,
        repo: str,
        dry_run: bool = False,
        *,
        high_signal_pr_numbers: set[int] | None = None,
    ) -> BuildReport:
        return self.load_records(
            _parse_jsonl(text),
            repo=repo,
            dry_run=dry_run,
            high_signal_pr_numbers=high_signal_pr_numbers,
        )

    def load_jsonl_file(
        self,
        path: str | Path,
        repo: str,
        dry_run: bool = False,
        *,
        high_signal_pr_numbers: set[int] | None = None,
    ) -> BuildReport:
        return self.load_jsonl_text(
            Path(path).read_text(encoding="utf-8"),
            repo,
            dry_run,
            high_signal_pr_numbers=high_signal_pr_numbers,
        )

    def load_records(
        self,
        records: Iterable[dict[str, Any]],
        repo: str,
        dry_run: bool = False,
        *,
        high_signal_pr_numbers: set[int] | None = None,
    ) -> BuildReport:
        materialized = list(records)
        for record in materialized:
            if record.get("repo") and record["repo"] != repo:
                raise InvalidRecordError(
                    f"record belongs to {record['repo']!r}, not {repo!r}",
                    component="graph",
                    record_id=record.get("record_id"),
                )
        plan = GraphPlan.from_records(
            materialized, high_signal_pr_numbers=high_signal_pr_numbers
        )
        report = self._report(plan, repo=repo, dry_run=dry_run)
        if dry_run:
            return report
        self._execute(plan)
        return report

    def rebuild_jsonl_text(
        self,
        text: str,
        repo: str,
        dry_run: bool = False,
        *,
        high_signal_pr_numbers: set[int] | None = None,
    ) -> BuildReport:
        return self.rebuild_records(
            _parse_jsonl(text),
            repo=repo,
            dry_run=dry_run,
            high_signal_pr_numbers=high_signal_pr_numbers,
        )

    def rebuild_jsonl_file(
        self,
        path: str | Path,
        repo: str,
        dry_run: bool = False,
        *,
        high_signal_pr_numbers: set[int] | None = None,
    ) -> BuildReport:
        return self.rebuild_jsonl_text(
            Path(path).read_text(encoding="utf-8"),
            repo,
            dry_run,
            high_signal_pr_numbers=high_signal_pr_numbers,
        )

    def rebuild_records(
        self,
        records: Iterable[dict[str, Any]],
        repo: str,
        dry_run: bool = False,
        *,
        high_signal_pr_numbers: set[int] | None = None,
    ) -> BuildReport:
        # Validate first: a bad corpus must not wipe an existing graph.
        materialized = list(records)
        plan = GraphPlan.from_records(
            materialized, high_signal_pr_numbers=high_signal_pr_numbers
        )
        report = self._report(plan, repo=repo, dry_run=dry_run)
        if dry_run:
            return report
        self.prepare_schema()
        self._delete_repo(repo)
        self._execute(plan)
        return report

    # -- internals ------------------------------------------------------
    def _delete_repo(self, repo: str) -> None:
        labels = [*dict.fromkeys(LABEL_BY_TYPE.values()), "Repo", "CodeRegion"]
        for label in labels:
            self.session.run(delete_nodes(label), repo=repo)

    def _execute(self, plan: GraphPlan) -> None:
        try:
            write_records(
                self.session, plan.to_records(), batch_size=self.batch_size
            )
        except Exception as error:  # noqa: BLE001 - re-raised with taxonomy
            if isinstance(error, GraphWriteError):
                raise
            raise GraphWriteError(
                f"graph write failed: {error}", component="graph"
            ) from error

    def _report(self, plan: GraphPlan, *, repo: str, dry_run: bool) -> BuildReport:
        counts = plan.counts()
        return BuildReport(
            nodes=counts["nodes"], rels=counts["rels"], dry_run=dry_run, repo=repo
        )


def _parse_jsonl(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as error:
            raise InvalidRecordError(
                f"invalid JSON on line {number}: {error.msg}", component="graph"
            ) from error
        if not isinstance(parsed, dict):
            raise InvalidRecordError(
                f"line {number} is not a JSON object", component="graph"
            )
        records.append(parsed)
    return records
