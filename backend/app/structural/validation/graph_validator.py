"""Structural validation: prove the extraction before trusting it.

Validation has three layers:

* ``validate_file`` / ``validate_batch`` — pure, pre-write checks on the
  extraction output (line bounds, ownership, duplicate identity).
* ``validate_graph`` — pure checks on the built node/relationship records,
  independent of any database. This is the Phase 10 acceptance gate: orphan
  files, invalid spans, broken references, duplicate IDs, config/import
  sanity, region coverage and review-anchor coverage.
* ``Neo4jStructuralValidator`` — the same checks re-expressed as Cypher,
  run only after a real load.

The pure layer is the gate. A graph that cannot pass ``validate_graph`` is
never written (D-0019), and the CLI refuses to advance to Component 4 while
``status`` is ``fail``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from app.structural.extraction.models import StructuralBatch, StructuralFile
from app.structural.graph.graph_records import StructuralGraph

ReportStatus = Literal["pass", "pass_with_warnings", "fail"]

# Files whose parser produces declarations but no symbols/regions still
# carry meaning (a `pyproject.toml` declares ConfigKeys). The plan allows an
# orphan when it is "explicitly allowed"; these languages are that allowlist,
# and only when the file actually owns at least one DECLARES edge.
_CONFIG_LANGUAGES = {"yaml", "toml", "json", "env"}

# For every relationship we know how to reason about, the legal endpoint
# labels. Unknown relationship types (the Component 2 vocabulary) are left
# alone so a merged graph is not falsely failed here.
_ENDPOINTS: dict[str, tuple[set[str], set[str]]] = {
    "DEFINES": ({"File"}, {"Symbol"}),
    "HAS_REGION": ({"File", "Symbol", "ChangedFile"}, {"CodeRegion"}),
    "IMPORTS": ({"File"}, {"File", "ExternalRef"}),
    "REFERENCES": ({"Symbol"}, {"Symbol"}),
    "USED_BY": ({"ConfigKey"}, {"Symbol"}),
    "DECLARES": ({"File"}, {"ConfigKey"}),
    "DOCUMENTED_BY": ({"File", "ConfigKey", "Symbol"}, {"Doc"}),
    "COMMENTS_ON": ({"ReviewComment"}, {"CodeRegion"}),
    "CHANGED_IN": ({"CodeRegion"}, {"Commit"}),
}

# A File is an orphan when none of these edges touch it.
_FILE_LIFELINES = {"DEFINES", "HAS_REGION", "DOCUMENTED_BY"}

_UNOWNED_SOURCES = {"changed_hunk", "review_comment"}


@dataclass
class ValidationReport:
    ok: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    # Checks that could not run because the input did not carry the data
    # (e.g. review anchors in a structural-only graph). Absence must be
    # reported as "not checked", never silently counted as a pass.
    not_checked: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def skip(self, check: str) -> None:
        if check not in self.not_checked:
            self.not_checked.append(check)

    @property
    def status(self) -> ReportStatus:
        if not self.ok:
            return "fail"
        if self.warnings or self.not_checked:
            return "pass_with_warnings"
        return "pass"

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "not_checked": list(self.not_checked),
            "stats": dict(self.stats),
        }

    def report(self) -> dict[str, Any]:
        """The flat Phase 10 report shape, stats hoisted to the top level."""
        payload = self.as_dict()
        return {**payload["stats"], **payload}


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

    seen_regions: set[str] = set()
    for region in entry.regions:
        if region.region_id in seen_regions:
            report.fail(f"duplicate region id {region.region_id}")
        seen_regions.add(region.region_id)
        if region.start_line < 1:
            report.fail(f"{region.region_id} starts before line 1")
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
        # An unresolved imported reference is allowed to remain target-less;
        # the batch resolver drops it before the graph is built.
        if reference.target_symbol_id is None:
            continue
        if reference.resolution == "same_file" and reference.target_symbol_id not in seen_symbols:
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
        combined.not_checked.extend(result.not_checked)
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


# ---------------------------------------------------------------------------
# Phase 10: pure graph validation
# ---------------------------------------------------------------------------


def validate_graph(
    graph: StructuralGraph,
    *,
    batch: StructuralBatch | None = None,
    high_signal_pr_numbers: set[int] | None = None,
) -> ValidationReport:
    """Run every structural acceptance check on the built graph records.

    ``batch`` (when supplied) lets the report distinguish *extracted but
    unresolved* imports/references — facts that never became edges and so
    are invisible in the graph alone. ``high_signal_pr_numbers`` enables the
    region-coverage check; without it that check is reported as not checked.
    """
    report = ValidationReport()
    nodes = graph.nodes
    edges = graph.edges

    ids: set[str] = set()
    label_by_id: dict[str, str] = {}
    props_by_id: dict[str, dict[str, Any]] = {}
    node_by_label: dict[str, list[Any]] = {}
    duplicates_by_label: dict[str, int] = {}

    for node in nodes:
        if node.key in ids:
            report.fail(f"duplicate node id {node.key}")
            duplicates_by_label[node.label] = duplicates_by_label.get(node.label, 0) + 1
        ids.add(node.key)
        label_by_id[node.key] = node.label
        props_by_id[node.key] = node.properties
        node_by_label.setdefault(node.label, []).append(node)

    # -- broken references + endpoint type sanity ------------------------
    touching: dict[str, set[str]] = {}
    for edge in edges:
        for endpoint in (edge.start_key, edge.end_key):
            touching.setdefault(endpoint, set()).add(edge.rel_type)
        if edge.start_key not in ids:
            report.fail(f"{edge.rel_type} has a missing source {edge.start_key}")
        if edge.end_key not in ids:
            report.fail(f"{edge.rel_type} has a missing target {edge.end_key}")
        allowed = _ENDPOINTS.get(edge.rel_type)
        if allowed is not None:
            source_labels, target_labels = allowed
            if edge.start_key in label_by_id and label_by_id[edge.start_key] not in source_labels:
                report.fail(
                    f"{edge.rel_type} starts at a {label_by_id[edge.start_key]}; "
                    f"expected {sorted(source_labels)}"
                )
            if edge.end_key in label_by_id and label_by_id[edge.end_key] not in target_labels:
                report.fail(
                    f"{edge.rel_type} targets a {label_by_id[edge.end_key]}; "
                    f"expected {sorted(target_labels)}"
                )

    # -- symbol ranges ---------------------------------------------------
    file_line_count: dict[str, int] = {}
    for node in node_by_label.get("File", []):
        count = _int(node.properties.get("line_count"))
        if count is not None:
            file_line_count[node.key] = count

    invalid_symbols = 0
    for node in node_by_label.get("Symbol", []):
        start = _int(node.properties.get("start_line"))
        end = _int(node.properties.get("end_line"))
        if start is None or end is None or start < 1 or end < start:
            invalid_symbols += 1
            report.fail(f"{node.key} has an invalid span ({start}..{end})")

    # -- region ranges ---------------------------------------------------
    invalid_regions = 0
    unowned_regions: list[str] = []
    for node in node_by_label.get("CodeRegion", []):
        start = _int(node.properties.get("start_line"))
        end = _int(node.properties.get("end_line"))
        if start is None or end is None or start < 1 or end < start:
            invalid_regions += 1
            report.fail(f"{node.key} has an invalid span ({start}..{end})")
            continue
        file_id = node.properties.get("file_id")
        limit = file_line_count.get(str(file_id)) if file_id else None
        if limit is not None and end > limit:
            invalid_regions += 1
            report.fail(
                f"{node.key} ends at line {end}, beyond file line count {limit}"
            )
        if "HAS_REGION" not in touching.get(node.key, set()):
            unowned_regions.append(node.key)
    if unowned_regions:
        report.warn(
            f"{len(unowned_regions)} CodeRegion node(s) have no HAS_REGION owner"
        )

    # -- config edge sanity (USED_BY needs a declared literal key) --------
    config_ids = {node.key for node in node_by_label.get("ConfigKey", [])}
    for edge in edges:
        if edge.rel_type != "USED_BY":
            continue
        if edge.start_key not in config_ids:
            report.fail(
                f"USED_BY starts at {edge.start_key}, which is not a declared ConfigKey"
            )
        if not _has_literal_value(props_by_id.get(edge.start_key, {})):
            report.warn(
                f"USED_BY on {edge.start_key} is not backed by a literal value"
            )

    # -- import sanity ---------------------------------------------------
    internal_import_edges = 0
    external_import_edges = 0
    for edge in edges:
        if edge.rel_type != "IMPORTS":
            continue
        if label_by_id.get(edge.end_key) == "File":
            internal_import_edges += 1
            target_props = props_by_id.get(edge.end_key, {})
            if _int(target_props.get("line_count")) == 0:
                report.warn(f"IMPORTS targets an empty File {edge.end_key}")
        elif label_by_id.get(edge.end_key) == "ExternalRef":
            external_import_edges += 1

    # -- orphan files ----------------------------------------------------
    orphans: list[str] = []
    allowed_orphans: list[str] = []
    for node in node_by_label.get("File", []):
        rels = touching.get(node.key, set())
        if rels & _FILE_LIFELINES:
            continue
        language = str(node.properties.get("language") or "")
        if language in _CONFIG_LANGUAGES and "DECLARES" in rels:
            allowed_orphans.append(node.key)
            continue
        orphans.append(node.key)
    if orphans:
        report.warn(
            f"{len(orphans)} File node(s) have no DEFINES/HAS_REGION/DOCUMENTED_BY edge"
        )

    orphan_docs = [
        node.key
        for node in node_by_label.get("Doc", [])
        if "DOCUMENTED_BY" not in touching.get(node.key, set())
    ]
    if orphan_docs:
        report.warn(
            f"{len(orphan_docs)} Doc node(s) are not reachable by DOCUMENTED_BY"
        )

    # -- region coverage for high-signal PRs -----------------------------
    high_signal_without_region: list[int] = []
    if high_signal_pr_numbers is None:
        report.skip("region_coverage")
    else:
        covered = {
            _int(node.properties.get("source_pr_number"))
            for node in node_by_label.get("CodeRegion", [])
        }
        high_signal_without_region = sorted(
            pr for pr in high_signal_pr_numbers if pr not in covered
        )
        if high_signal_without_region:
            report.fail(
                "high-signal PR(s) without a CodeRegion: "
                + ", ".join(str(pr) for pr in high_signal_without_region)
            )

    # -- review anchor coverage ------------------------------------------
    missing_review_anchors = 0
    review_comments = node_by_label.get("ReviewComment", [])
    if not review_comments:
        report.skip("review_anchor_coverage")
    else:
        comments_on: dict[str, list[str]] = {}
        for edge in edges:
            if edge.rel_type == "COMMENTS_ON":
                comments_on.setdefault(edge.start_key, []).append(edge.end_key)
        for comment in review_comments:
            anchor = _anchor(comment.properties)
            if anchor is None:
                continue
            regions = comments_on.get(comment.key, [])
            if not any(
                _region_contains(props_by_id.get(region, {}), anchor)
                for region in regions
            ):
                missing_review_anchors += 1
        if missing_review_anchors:
            report.fail(
                f"{missing_review_anchors} line-anchored review comment(s) "
                "have no matching or containing CodeRegion"
            )

    # -- extracted-but-unresolved facts ----------------------------------
    unresolved_imports, unresolved_references, external_imports = _unresolved_counts(batch)
    if batch is None:
        unresolved_imports = external_import_edges
        external_imports = external_import_edges
        unresolved_references = None
        report.skip("unresolved_references")

    report.stats = {
        "files_parsed": len(node_by_label.get("File", [])),
        "docs_created": len(node_by_label.get("Doc", [])),
        "symbols_created": len(node_by_label.get("Symbol", [])),
        "imports_created": internal_import_edges + external_import_edges,
        "config_keys_created": len(config_ids),
        "code_regions_created": len(node_by_label.get("CodeRegion", [])),
        "relationships_created": len(edges),
        "unresolved_imports": unresolved_imports,
        "external_imports": external_imports,
        "relative_unresolved_imports": max(0, unresolved_imports - external_imports),
        "internal_import_edges": internal_import_edges,
        "unresolved_references": unresolved_references,
        "orphans": len(orphans),
        "allowed_orphans": len(allowed_orphans),
        "orphan_docs": len(orphan_docs),
        "invalid_regions": invalid_regions,
        "invalid_symbols": invalid_symbols,
        "unowned_regions": len(unowned_regions),
        "duplicate_ids": sum(duplicates_by_label.values()),
        "missing_review_anchors": (
            missing_review_anchors if review_comments else None
        ),
        "high_signal_prs_missing_region": len(high_signal_without_region),
    }
    return report


def _unresolved_counts(
    batch: StructuralBatch | None,
) -> tuple[int, int | None, int]:
    """(unresolved imports, unresolved references, external imports).

    An import is *external* when it is absolute and resolved to nothing in
    the selected set — that is a normal third-party/stdlib package, not a
    failure. A *relative* import that resolves to nothing is a genuine
    unresolved edge (D-0015). Both are reported, but separately.
    """
    if batch is None:
        return 0, None, 0
    unresolved = 0
    external = 0
    unresolved_references = 0
    for entry in batch.files:
        for imported in entry.imports:
            if imported.resolved_path is not None:
                continue
            if imported.kind.value == "relative":
                unresolved += 1
            else:
                external += 1
        for reference in entry.references:
            if reference.target_symbol_id is None:
                unresolved_references += 1
    return unresolved + external, unresolved_references, external


def _has_literal_value(props: dict[str, Any]) -> bool:
    value = props.get("value")
    return value is not None and str(value).strip() != ""


def _anchor(props: dict[str, Any]) -> tuple[int, int] | None:
    line = _int(props.get("line"))
    start = _int(props.get("start_line"))
    if line is None and start is None:
        return None
    low = start if start is not None else line
    high = line if line is not None else start
    if low is None or high is None:
        return None
    if low > high:
        low, high = high, low
    return low, high


def _region_contains(props: dict[str, Any], anchor: tuple[int, int]) -> bool:
    start = _int(props.get("start_line"))
    end = _int(props.get("end_line"))
    if start is None or end is None:
        return False
    return start <= anchor[0] and anchor[1] <= end


def _int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# Post-write validation
# ---------------------------------------------------------------------------


class Neo4jStructuralValidator:
    """Post-write checks. Run after a real load, never in dry-run.

    The legacy ``report`` method keeps its two-query contract (orphan
    symbols, unowned regions) for existing callers. ``full_report`` runs the
    complete Phase 10 sweep as Cypher.
    """

    def __init__(self, session: Any) -> None:
        self.session = session

    # -- small helpers ---------------------------------------------------
    def _count(self, query: str, **params: Any) -> int:
        result = self.session.run(query, **params).single()
        return int(result["c"]) if result is not None else 0

    def count_by_label(self, label: str, repo: str) -> int:
        return self._count(
            f"MATCH (n:{label} {{repo: $repo}}) RETURN count(n) AS c", repo=repo
        )

    def orphan_symbols(self, repo: str) -> int:
        return self._count(
            "MATCH (s:Symbol {repo: $repo}) WHERE NOT (s)<-[:DEFINES]-(:File) "
            "RETURN count(s) AS c",
            repo=repo,
        )

    def regions_without_owner(self, repo: str) -> int:
        return self._count(
            "MATCH (r:CodeRegion {repo: $repo}) "
            "WHERE NOT (r)<-[:HAS_REGION]-(:File) "
            "AND NOT (r)<-[:HAS_REGION]-(:Symbol) "
            "RETURN count(r) AS c",
            repo=repo,
        )

    def orphan_files(self, repo: str) -> int:
        return self._count(
            "MATCH (f:File {repo: $repo}) "
            "WHERE NOT (f)-[:DEFINES]->(:Symbol) "
            "AND NOT (f)-[:HAS_REGION]->(:CodeRegion) "
            "AND NOT (f)-[:DOCUMENTED_BY]->(:Doc) "
            "AND NOT (f)-[:DECLARES]->(:ConfigKey) "
            "RETURN count(f) AS c",
            repo=repo,
        )

    def invalid_regions(self, repo: str) -> int:
        return self._count(
            "MATCH (r:CodeRegion {repo: $repo}) "
            "WHERE r.start_line IS NULL OR r.end_line IS NULL "
            "OR r.start_line < 1 OR r.end_line < r.start_line "
            "RETURN count(r) AS c",
            repo=repo,
        )

    def review_anchors_missing(self, repo: str) -> int:
        return self._count(
            "MATCH (c:ReviewComment {repo: $repo}) "
            "WHERE c.path IS NOT NULL AND (c.line IS NOT NULL OR c.start_line IS NOT NULL) "
            "AND NOT (c)-[:COMMENTS_ON]->(:CodeRegion) "
            "RETURN count(c) AS c",
            repo=repo,
        )

    def unresolved_imports(self, repo: str) -> int:
        return self._count(
            "MATCH (:File {repo: $repo})-[:IMPORTS]->(e:ExternalRef {repo: $repo}) "
            "RETURN count(e) AS c",
            repo=repo,
        )

    # -- complete sweep --------------------------------------------------
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

    def full_report(self, repo: str) -> ValidationReport:
        result = self.report(repo)
        invalid = self.invalid_regions(repo)
        if invalid:
            result.fail(f"{invalid} CodeRegion node(s) have impossible line spans")
        orphan_files = self.orphan_files(repo)
        if orphan_files:
            result.warn(f"{orphan_files} File node(s) are disconnected")
        anchors = self.review_anchors_missing(repo)
        if anchors:
            result.fail(
                f"{anchors} line-anchored ReviewComment node(s) have no CodeRegion"
            )
        result.stats.update(
            {
                "files_parsed": result.stats.get("File", 0),
                "symbols_created": result.stats.get("Symbol", 0),
                "code_regions_created": result.stats.get("CodeRegion", 0),
                "config_keys_created": result.stats.get("ConfigKey", 0),
                "orphans": orphan_files,
                "invalid_regions": invalid,
                "missing_review_anchors": anchors,
                "unresolved_imports": self.unresolved_imports(repo),
            }
        )
        return result


__all__ = [
    "Neo4jStructuralValidator",
    "ReportStatus",
    "ValidationReport",
    "validate_batch",
    "validate_file",
    "validate_graph",
]
