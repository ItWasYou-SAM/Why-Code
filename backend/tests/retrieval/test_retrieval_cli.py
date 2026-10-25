"""The CLI is a thin transport over the same service, with no hidden DB."""

from __future__ import annotations

import json

from retrieval_fixtures import (
    REPO,
    FakeBackend,
    FakeEmbedder,
    FakeGenerator,
    answer_json,
    candidate,
    context,
)

from app.retrieval.answer.answer_synthesizer import ANSWER_SCHEMA_NAME
from app.retrieval.cli import build_parser, main
from app.retrieval.config import RetrievalConfig
from app.retrieval.models import EntityType
from app.retrieval.question.entity_extractor import EntityIndex, KnownEntity
from app.retrieval.service import RetrievalService

INDEX = EntityIndex(
    entities=[
        KnownEntity(
            type=EntityType.CONFIG_KEY,
            identifier="retry.max_attempts",
            aliases=("retries", "retry limit"),
        )
    ]
)


def _service() -> RetrievalService:
    item = candidate("dec_001")
    return RetrievalService(
        backend=FakeBackend(
            exact=[item],
            fulltext=[item],
            vector=[item],
            contexts={"dec_001": context(item)},
        ),
        embedder=FakeEmbedder(),
        generator=FakeGenerator({ANSWER_SCHEMA_NAME: answer_json()}),
        config=RetrievalConfig(repo=REPO, parallel_retrieval=False),
        index=INDEX,
    )


def test_parser_defaults() -> None:
    args = build_parser().parse_args(["Why are retries disabled?"])
    assert args.repo is None
    assert args.max_evidence_items == 8
    assert args.max_answer_tokens == 700
    assert args.no_graph_path is False
    assert args.no_evidence is False


def test_main_prints_json_and_returns_zero(capsys) -> None:
    code = main(
        ["Why are retries disabled by default?", "--repo", REPO],
        service=_service(),
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer_status"] == "answered"
    assert payload["decision_ids"] == ["dec_001"]


def test_flags_reach_the_service(capsys) -> None:
    code = main(
        [
            "Why are retries disabled?",
            "--repo",
            REPO,
            "--no-evidence",
            "--no-graph-path",
            "--max-evidence-items",
            "1",
        ],
        service=_service(),
    )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["evidence"] == []
    assert payload["graph_path"] == []


def test_no_service_fails_closed(capsys) -> None:
    code = main(["Why are retries disabled?"])
    captured = capsys.readouterr()
    assert code == 2
    assert "no retrieval service configured" in captured.err
