"""Structural component CLI.

    python -m app.structural.cli select  --normalized ... --high-signal ...
    python -m app.structural.cli fetch   --normalized ... [--content-root DIR]
    python -m app.structural.cli extract --normalized ... --high-signal ... --content-root DIR
    python -m app.structural.cli build   --normalized ... --content-root DIR [--write]
    python -m app.structural.cli graph   --batch artifacts/structural/batch.json \
                                          --out artifacts/structural/structural_graph.jsonl
    python -m app.structural.cli validate --batch artifacts/structural/batch.json
    python -m app.structural.cli validate --uri $NEO4J_URI --database neo4j --repo encode/httpx
    python -m app.structural.cli write-graph \
        --input artifacts/structural/structural_graph.jsonl --uri $NEO4J_URI --database neo4j

The default is dry-run: no Neo4j connection is opened unless ``--write``
is passed and Neo4j settings are present.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.core.errors import AppError, InvalidRecordError
from app.graph.records import GraphRecords, records_from_jsonl, records_to_jsonl
from app.structural.config import StructuralConfig
from app.structural.content.content_store import ContentStore
from app.structural.content.file_content_fetcher import (
    CachedContentFetcher,
    LocalContentFetcher,
    RecordContentFetcher,
)
from app.structural.extraction.file_extractor import FileExtractor
from app.structural.extraction.models import StructuralBatch
from app.structural.graph.graph_records import build_structural_graph
from app.structural.pipeline import StructuralPipeline
from app.structural.selection.file_selector import FileSelector, load_jsonl
from app.structural.validation.graph_validator import (
    Neo4jStructuralValidator,
    ValidationReport,
    validate_batch,
    validate_graph,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    try:
        if args.command == "select":
            return _select(args)
        if args.command == "fetch":
            return _fetch(args)
        if args.command == "extract":
            return _extract(args)
        if args.command == "build":
            return _build(args)
        if args.command == "graph":
            return _graph(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "write-graph":
            return _write_graph(args)
    except AppError as error:
        print(f"{type(error).__name__}: {error}")
        return 1
    return 2


def _config(args: argparse.Namespace) -> StructuralConfig:
    return StructuralConfig(
        repo=args.repo,
        commit_sha=args.commit,
        include_tests=args.include_tests,
        include_ci=args.include_ci,
        include_docs=not args.no_docs,
        pinned_paths=args.pin or [],
        artifacts_dir=Path(args.artifacts_dir),
        content_dir=Path(args.artifacts_dir) / "structural" / "content",
    )


def _load(
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
    normalized = load_jsonl(args.normalized)
    # No --high-signal file means no high-signal filter at all. An empty
    # file means "nothing scored high", which selects nothing.
    high_signal = load_jsonl(args.high_signal) if args.high_signal else None
    return normalized, high_signal


def _selection(args: argparse.Namespace) -> Any:
    normalized, high_signal = _load(args)
    config = _config(args)
    return config, FileSelector(config).select(
        normalized_records=normalized,
        high_signal_prs=high_signal,
    )


def _select(args: argparse.Namespace) -> int:
    config, selection = _selection(args)
    out = Path(args.out or Path(args.artifacts_dir) / "structural" / "selection.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(selection.as_dict(), indent=2) + "\n", encoding="utf-8")
    print(f"selected {len(selection.files)} file(s); skipped {len(selection.skipped)}")
    print(out)
    return 0


def _pipeline(args: argparse.Namespace) -> StructuralPipeline:
    config, _ = _selection(args)
    fetcher = _fetcher(args, config)
    store = ContentStore(fetcher, config.content_dir)
    writer = None
    if getattr(args, "write", False):
        from app.core.config import Settings
        from app.graph.neo4j_driver import Neo4jClient
        from app.structural.graph.neo4j_writer import StructuralGraphWriter

        settings = Settings.from_env()
        client = Neo4jClient.from_settings(settings)
        writer = StructuralGraphWriter(client)
        # Constraints and indexes are idempotent (IF NOT EXISTS) and run
        # once before the first batch, so a load can never race its schema.
        writer.prepare_schema()
    return StructuralPipeline(
        config=config,
        fetcher=fetcher,
        extractor=FileExtractor(),
        writer=writer,
        content_store=store,
    )


def _fetcher(args: argparse.Namespace, config: StructuralConfig) -> Any:
    if getattr(args, "remote", False):
        from app.structural.content.file_content_fetcher import GitHubContentFetcher

        # Always the raw URL at the selected commit — never a branch name.
        return GitHubContentFetcher()
    if args.content_root:
        return CachedContentFetcher(
            LocalContentFetcher(args.content_root), config.content_dir
        )
    normalized, _ = _load(args)
    return RecordContentFetcher(normalized)


def _content_store(
    args: argparse.Namespace, config: StructuralConfig, fetcher: Any
) -> ContentStore:
    """The manifest doubles as the replay cache for already-stored files."""
    return ContentStore(
        fetcher,
        config.content_dir,
        manifest_path=_manifest_path(args, config),
    )


def _manifest_path(args: argparse.Namespace, config: StructuralConfig) -> Path:
    return Path(
        getattr(args, "manifest", None)
        or config.artifacts_dir / "structural" / "content_manifest.jsonl"
    )


def _extract(args: argparse.Namespace) -> int:
    config, selection = _selection(args)
    fetcher = _fetcher(args, config)
    store = _content_store(args, config, fetcher)
    pipeline = StructuralPipeline(
        config=config,
        fetcher=fetcher,
        extractor=FileExtractor(),
        content_store=store,
    )
    result = pipeline.run(selection)
    out = Path(args.out or Path(args.artifacts_dir) / "structural" / "batch.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        result.batch.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    manifest = _write_manifest(store, config)
    _print_summary(result)
    print(out)
    print(manifest)
    return 0 if result.validation.ok else 1


def _fetch(args: argparse.Namespace) -> int:
    config, selection = _selection(args)
    fetcher = _fetcher(args, config)
    store = _content_store(args, config, fetcher)
    skipped: list[dict[str, str]] = []
    for selected in selection.files:
        try:
            store.store(
                repo=selected.repo,
                path=selected.path,
                commit_sha=selected.commit_sha,
                language=selected.language,
                max_bytes=config.max_file_bytes,
            )
        except (InvalidRecordError, OSError) as error:
            skipped.append(
                {
                    "path": selected.path,
                    "reason": getattr(error, "message", str(error)),
                    "commit_sha": selected.commit_sha,
                }
            )
    out = Path(
        args.out or Path(args.artifacts_dir) / "structural" / "content_manifest.jsonl"
    )
    store.write_manifest(out)
    print(f"stored {len(store.records)} file(s); skipped {len(skipped)}")
    for item in skipped:
        print(f"SKIP {item['path']}: {item['reason']}")
    print(out)
    return 0


def _write_manifest(store: ContentStore, config: StructuralConfig) -> Path:
    out = config.artifacts_dir / "structural" / "content_manifest.jsonl"
    store.write_manifest(out)
    return out


def _build(args: argparse.Namespace) -> int:
    pipeline = _pipeline(args)
    _, selection = _selection(args)
    result = pipeline.run(selection)
    out = Path(args.out or Path(args.artifacts_dir) / "structural" / "build_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result.as_dict(), indent=2) + "\n", encoding="utf-8")
    if pipeline.content_store is not None:
        _write_manifest(pipeline.content_store, pipeline.config)
    _print_summary(result)
    print(out)
    return 0 if result.validation.ok else 1


def _validate(args: argparse.Namespace) -> int:
    report = _run_validation(args)
    payload = report.report()
    out = Path(args.out) if args.out else None
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    if out is not None:
        print(out)
    return 0 if report.ok else 1


def _run_validation(args: argparse.Namespace) -> ValidationReport:
    high_signal = _high_signal_numbers(args)
    if getattr(args, "uri", None):
        from app.graph.neo4j_driver import Neo4jClient

        if not args.repo:
            raise InvalidRecordError(
                "post-write validation needs --repo", component="structural"
            )
        client = Neo4jClient.connect(
            uri=args.uri,
            database=args.database,
            user=_neo4j_env("WHYCODE_NEO4J_USER"),
            password=_neo4j_env("WHYCODE_NEO4J_PASSWORD"),
        )
        try:
            return Neo4jStructuralValidator(client).full_report(args.repo)
        finally:
            client.close()

    batch: StructuralBatch | None = None
    graph = None
    if getattr(args, "batch", None):
        batch = StructuralBatch.model_validate(
            json.loads(Path(args.batch).read_text(encoding="utf-8"))
        )
        graph = build_structural_graph(batch)
    if getattr(args, "graph", None):
        records: GraphRecords = records_from_jsonl(
            Path(args.graph).read_text(encoding="utf-8").splitlines()
        )
        from app.structural.graph.graph_records import StructuralGraph

        graph = StructuralGraph(
            nodes=list(records.nodes), edges=list(records.relationships)
        )
    if graph is None:
        raise InvalidRecordError(
            "validate needs --batch, --graph or --uri", component="structural"
        )

    report = validate_batch(batch) if batch is not None else ValidationReport()
    graph_report = validate_graph(graph, batch=batch, high_signal_pr_numbers=high_signal)
    report.errors.extend(graph_report.errors)
    report.warnings.extend(graph_report.warnings)
    report.not_checked.extend(graph_report.not_checked)
    report.stats.update(graph_report.stats)
    if not graph_report.ok:
        report.ok = False
    return report


def _high_signal_numbers(args: argparse.Namespace) -> set[int] | None:
    path = getattr(args, "high_signal", None)
    if not path:
        return None
    numbers: set[int] = set()
    for row in load_jsonl(path):
        number = row.get("pr_number")
        if isinstance(number, int):
            numbers.add(number)
    return numbers


def _graph(args: argparse.Namespace) -> int:
    batch = StructuralBatch.model_validate(
        json.loads(Path(args.batch).read_text(encoding="utf-8"))
    )
    graph = build_structural_graph(batch)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        records_to_jsonl(graph.to_records()), encoding="utf-8"
    )
    counts = graph.counts()
    print(f"nodes={sum(counts['nodes'].values())} edges={len(graph.edges)}")
    print(out)
    return 0


def _write_graph(args: argparse.Namespace) -> int:
    records = records_from_jsonl(
        Path(args.input).read_text(encoding="utf-8").splitlines()
    )
    from app.graph.neo4j_driver import Neo4jClient
    from app.structural.graph.graph_records import StructuralGraph
    from app.structural.graph.neo4j_writer import StructuralGraphWriter

    graph = StructuralGraph(
        nodes=list(records.nodes), edges=list(records.relationships)
    )
    if args.dry_run:
        print(json.dumps(graph.counts(), indent=2))
        return 0
    if not args.uri:
        raise InvalidRecordError(
            "write-graph needs --uri (or --dry-run)", component="structural"
        )
    client = Neo4jClient.connect(
        uri=args.uri,
        database=args.database,
        user=_neo4j_env("WHYCODE_NEO4J_USER"),
        password=_neo4j_env("WHYCODE_NEO4J_PASSWORD"),
    )
    try:
        writer = StructuralGraphWriter(client)
        writer.prepare_schema()
        report = writer.write(graph, repo=args.repo)
    finally:
        client.close()
    print(json.dumps(report.as_dict(), indent=2))
    return 0


def _neo4j_env(name: str) -> str:
    import os

    value = os.environ.get(name, "").strip()
    if not value:
        raise InvalidRecordError(
            f"set {name} to talk to Neo4j", component="structural"
        )
    return value


def _print_summary(result: Any) -> None:
    stats = result.validation.stats
    print(
        f"files={stats.get('files', 0)} symbols={stats.get('symbols', 0)} "
        f"regions={stats.get('regions', 0)} imports={stats.get('imports', 0)} "
        f"config_keys={stats.get('config_keys', 0)} skipped={len(result.skipped)}"
    )
    if not result.validation.ok:
        for error in result.validation.errors:
            print(f"ERROR: {error}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.structural.cli")
    sub = parser.add_subparsers(dest="command")

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--repo", required=True)
        target.add_argument("--normalized", required=True)
        target.add_argument("--high-signal", default=None)
        target.add_argument("--commit", default=None)
        target.add_argument("--artifacts-dir", default="artifacts")
        target.add_argument("--out", default=None)
        target.add_argument("--content-root", default=None)
        target.add_argument(
            "--manifest",
            default=None,
            help="content manifest path (also used as the replay cache)",
        )
        target.add_argument(
            "--remote",
            action="store_true",
            help="fetch content from raw.githubusercontent.com at the commit",
        )
        target.add_argument("--pin", action="append", default=[])
        target.add_argument("--include-tests", action="store_true")
        target.add_argument("--include-ci", action="store_true")
        target.add_argument("--no-docs", action="store_true")

    select = sub.add_parser("select", help="write the file selection manifest")
    common(select)
    select.set_defaults(func=_select)

    fetch = sub.add_parser(
        "fetch", help="fetch file content at its commit into artifact storage"
    )
    common(fetch)
    fetch.set_defaults(func=_fetch)

    extract = sub.add_parser("extract", help="extract structure into a batch JSON")
    common(extract)
    extract.set_defaults(func=_extract)

    build = sub.add_parser("build", help="extract and optionally write to Neo4j")
    common(build)
    build.add_argument("--write", action="store_true")
    build.set_defaults(func=_build)

    validate = sub.add_parser(
        "validate", help="run the Phase 10 structural acceptance checks"
    )
    validate.add_argument("--batch", default=None, help="an extracted batch JSON")
    validate.add_argument(
        "--graph", default=None, help="a structural graph records JSONL"
    )
    validate.add_argument(
        "--high-signal",
        default=None,
        help="high-signal PR JSONL; enables the region-coverage check",
    )
    validate.add_argument("--repo", default=None)
    validate.add_argument(
        "--uri", default=None, help="Neo4j URI; validates the loaded graph"
    )
    validate.add_argument("--database", default="neo4j")
    validate.add_argument("--out", default=None)
    validate.set_defaults(func=_validate)

    graph = sub.add_parser(
        "graph",
        help="build structural graph records from an extracted batch (offline)",
    )
    graph.add_argument("--batch", required=True)
    graph.add_argument("--out", required=True)
    graph.set_defaults(func=_graph)

    write_graph = sub.add_parser(
        "write-graph", help="load a structural graph records JSONL into Neo4j"
    )
    write_graph.add_argument("--input", required=True)
    write_graph.add_argument("--repo", required=True)
    write_graph.add_argument("--uri", default=None)
    write_graph.add_argument("--database", default="neo4j")
    write_graph.add_argument("--dry-run", action="store_true")
    write_graph.set_defaults(func=_write_graph)
    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
