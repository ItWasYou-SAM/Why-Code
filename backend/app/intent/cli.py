"""Intent-extraction CLI.

    python -m app.intent.cli detect  --repo encode/httpx \
        --normalized artifacts/normalized.jsonl \
        --high-signal artifacts/high_signal_prs.jsonl

    python -m app.intent.cli extract --repo encode/httpx \
        --normalized artifacts/normalized.jsonl \
        --high-signal artifacts/high_signal_prs.jsonl \
        --db artifacts/evidence.db \
        --structural-graph artifacts/structural/structural_graph.jsonl

    python -m app.intent.cli write --input artifacts/intent/intent_graph.jsonl \
        --repo encode/httpx --dry-run

Extraction is dry-run by default: no Neo4j connection is opened unless
``--write`` is passed and Neo4j settings are present.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.core.errors import AppError, InvalidRecordError
from app.graph.records import GraphRecords, records_from_jsonl, records_to_jsonl
from app.ingestion.store import EvidenceStore
from app.intent.config import IntentConfig
from app.intent.evaluation.golden import default_golden_path
from app.intent.evaluation.metrics import EvaluationReport
from app.intent.evaluation.runner import evaluate_files
from app.intent.graph.intent_records import IntentGraph
from app.intent.llm.provider import build_provider
from app.intent.records import load_jsonl
from app.intent.review.review_queue import REVIEW_ACTIONS, ReviewQueue
from app.intent.selection.candidate_detector import CandidateDetector
from app.intent.selection.evidence_selector import EvidenceSelector
from app.intent.validation.graph_link_validator import GraphIndex


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        if args.command == "detect":
            return _detect(args)
        if args.command == "extract":
            return _extract(args)
        if args.command == "write":
            return _write(args)
        if args.command == "validate":
            return _validate(args)
        if args.command == "review":
            return _review(args)
        if args.command == "evaluate":
            return _evaluate(args)
    except AppError as error:
        print(f"{type(error).__name__}: {error}")
        return 1
    return 2


def _config(args: argparse.Namespace) -> IntentConfig:
    config = IntentConfig.from_env(args.repo, artifacts_dir=args.artifacts_dir)
    if getattr(args, "model", None):
        config = config.model_copy(update={"model": args.model})
    if getattr(args, "max_context_tokens", None):
        config = config.model_copy(
            update={"max_context_tokens": args.max_context_tokens}
        )
    return config


def _bundles(args: argparse.Namespace) -> tuple[list[Any], list[dict[str, Any]]]:
    normalized = load_jsonl(args.normalized)
    high_signal = load_jsonl(args.high_signal) if args.high_signal else None
    selector = EvidenceSelector(
        args.repo,
        high_signal_pr_numbers=None,
        max_prs=getattr(args, "max_prs", None),
    )
    bundles = selector.select(normalized, high_signal_records=high_signal)
    return bundles, normalized


def _index(
    args: argparse.Namespace, normalized: list[dict[str, Any]]
) -> GraphIndex:
    structural: GraphRecords | None = None
    path = getattr(args, "structural_graph", None)
    if path:
        structural = records_from_jsonl(
            Path(path).read_text(encoding="utf-8").splitlines()
        )
    return GraphIndex.from_sources(
        repo=args.repo,
        normalized_records=normalized,
        structural_records=structural,
    )


def _detect(args: argparse.Namespace) -> int:
    bundles, _ = _bundles(args)
    detector = CandidateDetector()
    report = []
    for bundle in bundles:
        candidates = detector.detect(bundle)
        report.append(
            {
                "pr_number": bundle.pr_number,
                "title": bundle.pr.payload.get("title", ""),
                "candidates": [candidate.as_dict() for candidate in candidates],
            }
        )
    out = Path(
        args.out or Path(args.artifacts_dir) / "intent" / "candidates.json"
    )
    _write_json(out, report)
    actionable = sum(
        1
        for row in report
        for candidate in row["candidates"]
        if candidate["candidate_class"] != "LOW_SIGNAL"
    )
    print(f"{len(bundles)} PR(s); {actionable} actionable candidate(s)")
    print(out)
    return 0


def _extract(args: argparse.Namespace) -> int:
    from app.intent.pipeline import IntentPipeline

    config = _config(args)
    bundles, normalized = _bundles(args)
    index = _index(args, normalized)
    if not Path(args.db).exists():
        raise InvalidRecordError(
            f"no evidence store at {args.db}", component="intent"
        )
    store = EvidenceStore(args.db)
    provider = build_provider(config)
    pipeline = IntentPipeline(
        config=config,
        store=store,
        index=index,
        provider=provider,
    )
    try:
        result = pipeline.run(bundles)
    finally:
        store.close()

    intent_dir = config.resolve_intent_dir()
    intent_dir.mkdir(parents=True, exist_ok=True)
    batch_path = Path(args.out or intent_dir / "intent_batch.json")
    graph_path = intent_dir / "intent_graph.jsonl"
    review_path = Path(args.review_out or intent_dir / "review_queue.jsonl")
    report_path = intent_dir / "extraction_report.json"

    _write_json(batch_path, result.as_dict())
    graph_path.write_text(
        records_to_jsonl(result.graph.to_records()) if result.graph else "",
        encoding="utf-8",
    )
    if result.review_queue is not None:
        result.review_queue.write_jsonl(review_path)
    _write_json(
        report_path,
        {
            "repo": config.repo,
            "summary": result.summary.as_dict(),
            "review_counts": (
                result.review_queue.counts() if result.review_queue else {}
            ),
            "graph_counts": result.graph.counts() if result.graph else {},
        },
    )

    if args.write:
        _write_to_neo4j(args, result)

    print(
        f"{config.repo}: {result.summary.proposals} intent(s), "
        f"{len(result.incidents)} incident(s), "
        f"{result.summary.review_items} review item(s)"
    )
    for path in (batch_path, graph_path, review_path, report_path):
        print(path)
    return 0


def _write_to_neo4j(args: argparse.Namespace, result: Any) -> None:
    from app.graph.neo4j_driver import Neo4jClient
    from app.intent.graph.neo4j_writer import IntentGraphWriter

    if result.graph is None:
        raise InvalidRecordError("nothing to write", component="intent")
    client = Neo4jClient.connect(
        uri=_env("WHYCODE_NEO4J_URI"),
        database=os.environ.get("WHYCODE_NEO4J_DATABASE", "neo4j"),
        user=_env("WHYCODE_NEO4J_USER"),
        password=_env("WHYCODE_NEO4J_PASSWORD"),
    )
    try:
        writer = IntentGraphWriter(client)
        writer.prepare_schema()
        writer.write(result.graph, repo=args.repo, verify_evidence=True)
        if result.status_updates:
            writer.apply_status_updates(result.status_updates)
    finally:
        client.close()


def _write(args: argparse.Namespace) -> int:
    records = records_from_jsonl(
        Path(args.input).read_text(encoding="utf-8").splitlines()
    )
    graph = IntentGraph(
        repo=args.repo,
        nodes=list(records.nodes),
        relationships=list(records.relationships),
    )
    if args.dry_run:
        print(json.dumps(graph.counts(), indent=2))
        return 0
    if not args.uri:
        raise InvalidRecordError(
            "write needs --uri (or --dry-run)", component="intent"
        )
    from app.graph.neo4j_driver import Neo4jClient
    from app.intent.graph.neo4j_writer import IntentGraphWriter

    client = Neo4jClient.connect(
        uri=args.uri,
        database=args.database,
        user=_env("WHYCODE_NEO4J_USER"),
        password=_env("WHYCODE_NEO4J_PASSWORD"),
    )
    try:
        writer = IntentGraphWriter(client)
        writer.prepare_schema()
        report = writer.write(graph, repo=args.repo)
    finally:
        client.close()
    print(json.dumps(report.as_dict(), indent=2))
    return 0


def _validate(args: argparse.Namespace) -> int:
    records = records_from_jsonl(
        Path(args.input).read_text(encoding="utf-8").splitlines()
    )
    graph = IntentGraph(
        repo=args.repo,
        nodes=list(records.nodes),
        relationships=list(records.relationships),
    )
    graph.assert_integrity()
    counts = graph.counts()
    payload = {
        "status": "pass",
        "nodes": counts["nodes"],
        "rels": counts["rels"],
        "node_count": len(graph.nodes),
        "relationship_count": len(graph.relationships),
    }
    print(json.dumps(payload, indent=2))
    if args.out:
        _write_json(Path(args.out), payload)
    return 0


def _review(args: argparse.Namespace) -> int:
    items = ReviewQueue.from_jsonl(args.queue, repo=args.repo)
    item = next(
        (entry for entry in items if entry.candidate_id == args.candidate),
        None,
    )
    if item is None:
        raise InvalidRecordError(
            f"no review item {args.candidate}",
            component="intent",
            record_id=args.candidate,
        )
    queue = ReviewQueue(repo=args.repo)
    queue.items = [item]
    updated = queue.apply(
        args.candidate,
        args.action,
        claim=args.claim,
        status=args.status,
    )
    out = queue.write_jsonl(args.queue)
    print(json.dumps(updated.model_dump(mode="json"), indent=2, sort_keys=True))
    print(out)
    return 0


def _evaluate(args: argparse.Namespace) -> int:
    golden = Path(args.golden) if args.golden else default_golden_path()
    reports = evaluate_files(
        predictions_path=args.predictions,
        golden_path=golden,
        repo=args.repo,
        corpus=args.corpus,
    )
    out = Path(
        args.out
        or Path(args.artifacts_dir) / "intent" / "evaluation_report.json"
    )
    if args.corpus == "all":
        payload: Any = {
            "repo": args.repo,
            "golden": str(golden),
            "predictions": args.predictions,
            "corpora": {
                name: report.model_dump(mode="json")
                for name, report in reports.items()
            },
        }
        _write_json(out, payload)
        for name, report in reports.items():
            _print_metric_line(name, report)
    else:
        report = reports[args.corpus]
        _write_json(out, report.model_dump(mode="json"))
        _print_metric_line(args.corpus, report)
    print(out)
    return 0


def _print_metric_line(corpus: str, report: EvaluationReport) -> None:
    metrics = report.metrics
    parts = [
        f"decision precision={_metric(metrics.decision_precision.value)}",
        f"decision recall={_metric(metrics.decision_recall.value)}",
        f"candidate recall={_metric(metrics.candidate_recall.value)}",
        f"evidence precision={_metric(metrics.evidence_precision.value)}",
        f"refusal correctness={_metric(metrics.refusal_correctness.value)}",
    ]
    print(f"{corpus}: " + ", ".join(parts))


def _metric(value: float | None) -> str:
    return "not checked" if value is None else f"{value:.2f}"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise InvalidRecordError(f"set {name} to talk to Neo4j", component="intent")
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app.intent.cli")
    sub = parser.add_subparsers(dest="command")

    def common(target: argparse.ArgumentParser) -> None:
        target.add_argument("--repo", required=True)
        target.add_argument("--normalized", required=True)
        target.add_argument("--high-signal", default=None)
        target.add_argument("--structural-graph", default=None)
        target.add_argument("--artifacts-dir", default="artifacts")
        target.add_argument("--out", default=None)
        target.add_argument("--review-out", default=None)
        target.add_argument("--model", default=None)
        target.add_argument("--max-prs", type=int, default=None)
        target.add_argument("--max-context-tokens", type=int, default=None)

    detect = sub.add_parser("detect", help="write candidate detection report")
    common(detect)
    detect.set_defaults(func=_detect)

    extract = sub.add_parser(
        "extract", help="run the intent extraction pipeline (dry-run by default)"
    )
    common(extract)
    extract.add_argument("--db", required=True, help="SQLite evidence store")
    extract.add_argument("--write", action="store_true", help="also write to Neo4j")
    extract.set_defaults(func=_extract)

    write = sub.add_parser("write", help="load an intent graph JSONL into Neo4j")
    write.add_argument("--input", required=True)
    write.add_argument("--repo", required=True)
    write.add_argument("--uri", default=None)
    write.add_argument("--database", default="neo4j")
    write.add_argument("--dry-run", action="store_true")
    write.set_defaults(func=_write)

    validate = sub.add_parser("validate", help="validate an intent graph JSONL")
    validate.add_argument("--input", required=True)
    validate.add_argument("--repo", required=True)
    validate.add_argument("--out", default=None)
    validate.set_defaults(func=_validate)

    review = sub.add_parser(
        "review", help="apply a human review decision to the review queue"
    )
    review.add_argument("--queue", required=True, help="review_queue.jsonl")
    review.add_argument("--repo", required=True)
    review.add_argument(
        "--action",
        required=True,
        choices=list(REVIEW_ACTIONS),
        help="approve | reject | edit_claim | change_status | mark_contested | mark_duplicate",
    )
    review.add_argument("--candidate", required=True, help="candidate_id")
    review.add_argument("--claim", default=None, help="replacement claim (edit_claim)")
    review.add_argument("--status", default=None, help="replacement status (change_status)")
    review.set_defaults(func=_review)

    evaluate = sub.add_parser(
        "evaluate",
        help="score an intent run against the human-curated golden decisions",
    )
    evaluate.add_argument(
        "--predictions",
        required=True,
        help="intent_batch.json, intent JSONL, or a hand-written prediction file",
    )
    evaluate.add_argument(
        "--golden",
        default=None,
        help="golden decisions JSONL (defaults to the bundled set)",
    )
    evaluate.add_argument("--repo", default="encode/httpx")
    evaluate.add_argument(
        "--corpus",
        default="all",
        help="fixture, httpx-public, or all (default: all)",
    )
    evaluate.add_argument("--artifacts-dir", default="artifacts")
    evaluate.add_argument("--out", default=None)
    evaluate.set_defaults(func=_evaluate)
    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
