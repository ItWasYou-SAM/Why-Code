"""Structural validation: prove the extraction before trusting it.

Validation is split in two:

* ``validate_batch`` — pure, pre-write checks on extraction output.
* ``Neo4jStructuralValidator`` — post-write queries against the real graph.

The pure checks are the acceptance gate: line bounds are valid, symbols
belong to their file, every region is reachable, and every import edge is
either a real file or an explicit ExternalRef.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.structural.extraction.models import StructuralBatch, StructuralFile
from app.structural.graph.graph_records import StructuralGraph


@dataclass
class ValidationReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def fail(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "stats": dict(self.stats),
        }


def validate_file(entry: StructuralFile) -> ValidationReport:
    report = ValidationReport()
    line_count = entry.line_count

    seen_symbols: set[str] = set()
    for symbol in entry.symbols:
        if symbol.symbol_id in seen_symbols:
            report.fail(f"duplicate symbol id {symbol.symbol_id}")
        seen_symbols.add(symbol.symbol_id)
        if symbol.start_line < 1:
            report.fail(f"{symbol.symbol_id} starts before line 1")
        if symbol.end_line < symbol.start_line:
            report.fail(f"{symbol.symbol_id} ends before it starts")
        if line_count is not None and symbol.end_line > line_count:
            report.fail(
                f"{symbol.symbol_id} ends at line {symbol.end_line}, beyond "
                f"the file's line count of {line_count}"
            )
        if symbol.path != entry.path:
            report.fail(f"{symbol.symbol_id} belongs to {symbol.path}, not {entry.path}")
        if symbol.commit_sha != entry.commit_sha:
            report.fail(f"{symbol.symbol_id} has a mismatched commit sha")

    for region in entry.regions:
        if region.end_line < region.start_line:
            report.fail(f"{region.region_id} ends before it starts")
        if line_count is not None and region.end_line > line_count:
            report.fail(f"{region.region_id} exceeds the file's line count")
        if region.symbol_qualname and region.symbol_qualname not in {
            symbol.qualname for symbol in entry.symbols
        }:
            report.fail(
                f"{region.region_id} names unknown symbol {region.symbol_qualname}"
            )

    for imported in entry.imports:
        if imported.line < 1:
            report.fail(f"import {imported.module!r} has an invalid line")
        if imported.kind.value == "relative" and imported.level < 1:
            report.fail(f"relative import {imported.module!r} has no level")

    for reference in entry.references:
        if reference.target_symbol_id not in seen_symbols:
            report.fail(
                f"reference from {reference.source_qualname} targets "
                f"unknown symbol {reference.target_symbol_id}"
            )

    report.stats = {
        "symbols": len(entry.symbols),
        "imports": len(entry.imports),
        "regions": len(entry.regions),
        "config_keys": len(entry.config_keys),
        "config_references": len(entry.config_references),
        "references": len(entry.references),
    }
    return report


def validate_batch(batch: StructuralBatch) -> ValidationReport:
    combined = ValidationReport()
    for entry in batch.files:
        result = validate_file(entry)
        combined.errors.extend(result.errors)
        combined.warnings.extend(result.warnings)
        if not result.ok:
            combined.ok = False
    combined.stats = {
        "files": len(batch.files),
        "docs": len(batch.docs),
        "skipped": len(batch.skipped),
        "symbols": sum(len(entry.symbols) for entry in batch.files),
        "regions": sum(len(entry.regions) for entry in batch.files),
        "imports": sum(len(entry.imports) for entry in batch.files),
        "config_keys": sum(len(entry.config_keys) for entry in batch.files),
    }
    return combined


def validate_graph(graph: StructuralGraph) -> ValidationReport:
    report = ValidationReport()
    ids = graph.node_ids()
    labels = {node.node_id: node.label for node in graph.nodes}
    for edge in graph.edges:
        if edge.from_id not in ids:
            report.fail(f"{edge.rel_type} has a missing source {edge.from_id}")
        if edge.to_id not in ids:
            report.fail(f"{edge.rel_type} has a missing target {edge.to_id}")
        if edge.rel_type == "IMPORTS" and edge.to_id in labels:
            target_label = labels[edge.to_id]
            if target_label not in {"File", "ExternalRef"}:
                report.fail(
                    f"IMPORTS targets a {target_label}; only File or ExternalRef "
                    "edges are allowed"
                )
    report.stats = {
        "nodes": len(graph.nodes),
        "edges": len(graph.edges),
        "skipped": len(graph.skipped),
    }
    return report


class Neo4jStructuralValidator:
    """Post-write checks. Run after a real load, never in dry-run."""

    def __init__(self, session: Any) -> None:
        self.session = session

    def count_by_label(self, label: str, repo: str) -> int:
        result = self.session.run(
            f"MATCH (n:{label} {{repo: $repo}}) RETURN count(n) AS c", repo=repo
        ).single()
        return int(result["c"]) if result is not None else 0

    def orphan_symbols(self, repo: str) -> int:
        result = self.session.run(
            "MATCH (s:Symbol {repo: $repo}) WHERE NOT (s)<-[:DEFINES]-(:File) "
            "RETURN count(s) AS c",
            repo=repo,
        ).single()
        return int(result["c"]) if result is not None else 0

    def regions_without_owner(self, repo: str) -> int:
        result = self.session.run(
            "MATCH (r:CodeRegion {repo: $repo}) "
            "WHERE NOT (r)<-[:HAS_REGION]-(:File) "
            "AND NOT (r)<-[:HAS_REGION]-(:Symbol) "
            "RETURN count(r) AS c",
            repo=repo,
        ).single()
        return int(result["c"]) if result is not None else 0

    def report(self, repo: str) -> ValidationReport:
        result = ValidationReport()
        orphans = self.orphan_symbols(repo)
        if orphans:
            result.fail(f"{orphans} Symbol node(s) are not defined by any File")
        unowned = self.regions_without_owner(repo)
        if unowned:
            result.fail(f"{unowned} CodeRegion node(s) have no owning File or Symbol")
        result.stats = {
            label: self.count_by_label(label, repo)
            for label in ("File", "Symbol", "CodeRegion", "ConfigKey", "ExternalRef", "Doc")
        }
        return result


__all__ = [
    "Neo4jStructuralValidator",
    "ValidationReport",
    "validate_batch",
    "validate_file",
    "validate_graph",
]
