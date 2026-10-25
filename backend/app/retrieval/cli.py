"""Command-line transports for retrieval.

The ask path is a thin transport over :class:`RetrievalService`; it never
constructs a backend or an LLM on its own. The ``understand`` path is an
offline Phase 1 inspector that can read a saved catalog JSON instead of a
live Neo4j.

    python -m app.retrieval.cli "Why are retries disabled by default?" \\
        --repo encode/httpx

    python -m app.retrieval.cli understand \\
        --repo encode/httpx \\
        --question "Why are retries disabled by default?" \\
        --catalog-json catalog.json --no-llm
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.errors import AppError, InvalidRecordError
from app.graph.neo4j_driver import Neo4jClient
from app.retrieval.config import RetrievalConfig
from app.retrieval.models import AskParameters
from app.retrieval.question.entity_catalog import (
    EntityCatalog,
    Neo4jEntityCatalog,
    StaticEntityCatalog,
)
from app.retrieval.question.entity_extractor import EntityExtractor
from app.retrieval.question.models import CatalogEntity
from app.retrieval.question.query_builder import build_retrieval_query

if TYPE_CHECKING:
    from app.retrieval.service import RetrievalService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.retrieval.cli",
        description="Ask a repository question through the retrieval service.",
    )
    parser.add_argument("question")
    parser.add_argument("--repo")
    parser.add_argument("--max-evidence-items", type=int, default=8)
    parser.add_argument("--max-answer-tokens", type=int, default=700)
    parser.add_argument("--no-graph-path", action="store_true")
    parser.add_argument("--no-evidence", action="store_true")
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    service: RetrievalService | None = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        if arguments and arguments[0] == "understand":
            return _understand(_understand_parser().parse_args(arguments[1:]))
        args = build_parser().parse_args(arguments)
        if service is None:
            print("no retrieval service configured", file=sys.stderr)
            return 2
        response = service.ask(
            AskParameters(
                question=args.question,
                repo=args.repo,
                max_evidence_items=args.max_evidence_items,
                max_answer_tokens=args.max_answer_tokens,
                include_graph_path=not args.no_graph_path,
                include_evidence=not args.no_evidence,
            )
        )
        print(json.dumps(response.model_dump(mode="json"), indent=2))
        return 0
    except (AppError, ValueError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1


def _understand_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.retrieval.cli understand",
        description="Inspect Phase 1 question understanding.",
    )
    parser.add_argument("--repo", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument(
        "--catalog-json",
        help="JSON list of catalog entries; default is a live Neo4j load",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="disable the constrained LLM entity fallback",
    )
    parser.add_argument("--out", help="optional output JSON path")
    return parser


def _understand(args: argparse.Namespace) -> int:
    config = RetrievalConfig.from_env(args.repo)
    if args.no_llm:
        config = config.model_copy(
            update={"enable_llm_entity_extraction": False}
        )
    catalog = _load_catalog(args.repo, args.catalog_json, config)
    understanding = EntityExtractor(catalog, config).extract(args.question)
    query = build_retrieval_query(
        understanding, max_terms=config.fulltext_max_terms
    )
    payload = {
        "understanding": understanding.as_dict(),
        "query": query.as_dict(),
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _load_catalog(
    repo: str,
    catalog_json: str | None,
    config: RetrievalConfig,
) -> EntityCatalog:
    if catalog_json:
        return _catalog_from_json(repo, Path(catalog_json))
    return _catalog_from_neo4j(repo, config)


def _catalog_from_json(repo: str, path: Path) -> StaticEntityCatalog:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise InvalidRecordError(
            f"cannot read catalog JSON: {error}", component="retrieval"
        ) from error
    rows = payload.get("entities") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise InvalidRecordError(
            "catalog JSON must be a list or an object with an 'entities' list",
            component="retrieval",
        )
    entities = [CatalogEntity.model_validate(row) for row in rows]
    return StaticEntityCatalog(repo, entities)


def _catalog_from_neo4j(
    repo: str, config: RetrievalConfig
) -> StaticEntityCatalog:
    uri = os.environ.get("WHYCODE_NEO4J_URI", "").strip()
    user = os.environ.get("WHYCODE_NEO4J_USER", "").strip()
    password = os.environ.get("WHYCODE_NEO4J_PASSWORD", "").strip()
    database = os.environ.get("WHYCODE_NEO4J_DATABASE", "neo4j").strip()
    if not uri or not user or not password:
        raise InvalidRecordError(
            "WHYCODE_NEO4J_URI, WHYCODE_NEO4J_USER and "
            "WHYCODE_NEO4J_PASSWORD are required without --catalog-json",
            component="retrieval",
        )
    client = Neo4jClient.connect(
        uri=uri,
        database=database or "neo4j",
        user=user,
        password=password,
    )
    try:
        return Neo4jEntityCatalog.load(
            client,
            repo,
            limit_per_type=config.catalog_limit_per_type,
        )
    finally:
        client.close()


__all__ = ["build_parser", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
