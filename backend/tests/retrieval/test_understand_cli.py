"""CLI contract for offline Phase 1 inspection."""

from __future__ import annotations

import json
from pathlib import Path

from fixtures.graph_fixtures import REPO, SYMBOL_SEND_KEY, catalog_entities

from app.retrieval.cli import main


def test_understand_reads_a_catalog_json(
    tmp_path: Path, capsys: object
) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            [
                entity.model_dump(mode="json")
                for entity in catalog_entities()
            ]
        ),
        encoding="utf-8",
    )
    code = main(
        [
            "understand",
            "--repo",
            REPO,
            "--question",
            "Why does Client.send not retry requests?",
            "--catalog-json",
            str(catalog_path),
            "--no-llm",
        ]
    )
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert code == 0
    payload = json.loads(captured.out)
    assert payload["understanding"]["question_type"] == "CODE_WHY"
    assert payload["query"]["exact_identifiers"][0]["graph_key"] == SYMBOL_SEND_KEY


def test_understand_rejects_a_bad_catalog(tmp_path: Path) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text("{}", encoding="utf-8")
    code = main(
        [
            "understand",
            "--repo",
            REPO,
            "--question",
            "Why does Client.send not retry?",
            "--catalog-json",
            str(catalog_path),
            "--no-llm",
        ]
    )
    assert code == 1
