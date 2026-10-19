"""CLI contracts for the content-fetch phase."""

from __future__ import annotations

import json

from structural_helpers import COMMIT, REPO

from app.structural.cli import main


def write_normalized(tmp_path, paths=("httpx/_client.py",)):
    normalized = tmp_path / "normalized.jsonl"
    records = []
    for path in paths:
        records.append(
            {
                "record_id": f"httpx:pr:1:file:{path}",
                "record_type": "changed_file",
                "repo": REPO,
                "pr_number": 1,
                "payload": {"path": path},
                "content_hash": "x",
                "ingested_at": "2026-10-19T10:00:00Z",
            }
        )
    normalized.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )
    return normalized


class CountingFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, *, repo, path, commit_sha):
        self.calls += 1
        return "x = 1\n"


def _patch_remote(monkeypatch, fetcher):
    """`_fetcher` imports the class lazily, so patch it at the source module."""
    import app.structural.content.file_content_fetcher as source

    monkeypatch.setattr(source, "GitHubContentFetcher", lambda: fetcher)


def _fetch_argv(normalized, artifacts):
    return [
        "fetch",
        "--repo",
        REPO,
        "--normalized",
        str(normalized),
        "--commit",
        COMMIT,
        "--artifacts-dir",
        str(artifacts),
        "--remote",
    ]


def test_fetch_writes_blobs_and_a_manifest(tmp_path, monkeypatch) -> None:
    fetcher = CountingFetcher()
    _patch_remote(monkeypatch, fetcher)
    normalized = write_normalized(tmp_path)
    artifacts = tmp_path / "artifacts"
    assert main(_fetch_argv(normalized, artifacts)) == 0

    manifest = artifacts / "structural" / "content_manifest.jsonl"
    payload = json.loads(manifest.read_text().splitlines()[0])
    assert payload["commit_sha"] == COMMIT
    assert "content" not in payload
    assert (artifacts / "structural" / "content" / payload["blob_path"]).is_file()
    assert fetcher.calls == 1


def test_second_fetch_replays_without_network(tmp_path, monkeypatch) -> None:
    first = CountingFetcher()
    _patch_remote(monkeypatch, first)
    normalized = write_normalized(tmp_path)
    artifacts = tmp_path / "artifacts"
    argv = _fetch_argv(normalized, artifacts)
    assert main(argv) == 0
    assert first.calls == 1

    second = CountingFetcher()
    _patch_remote(monkeypatch, second)
    assert main(argv) == 0
    assert second.calls == 0, "a replayed fetch must not hit the network"
    assert len(list((artifacts / "structural" / "content").rglob("*.txt"))) == 1


def test_manifest_is_honored_as_the_replay_source(tmp_path, monkeypatch) -> None:
    fetcher = CountingFetcher()
    _patch_remote(monkeypatch, fetcher)
    normalized = write_normalized(tmp_path)
    artifacts = tmp_path / "artifacts"
    main(_fetch_argv(normalized, artifacts))
    manifest = artifacts / "structural" / "content_manifest.jsonl"
    before = manifest.read_text()

    second = CountingFetcher()
    _patch_remote(monkeypatch, second)
    main(_fetch_argv(normalized, artifacts))
    assert second.calls == 0
    assert manifest.read_text() == before, "replay must not duplicate manifest rows"
