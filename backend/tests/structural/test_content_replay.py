"""Replay contracts: an existing manifest means no second fetch."""

from __future__ import annotations

import hashlib

import pytest
from structural_helpers import COMMIT, REPO

from app.core.errors import InvalidRecordError
from app.structural.content.content_store import ContentStore
from app.structural.extraction.models import Language

SOURCE = "x = 1\n"


class CountingFetcher:
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.mapping = mapping or {}
        self.calls = 0

    def fetch(self, *, repo: str, path: str, commit_sha: str) -> str:
        self.calls += 1
        return self.mapping.get(path, SOURCE)


def test_manifest_replay_does_not_refetch(tmp_path) -> None:
    manifest = tmp_path / "content_manifest.jsonl"
    first = ContentStore(CountingFetcher(), tmp_path / "blobs")
    first.store(repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON)
    first.write_manifest(manifest)

    second_fetcher = CountingFetcher()
    second = ContentStore(
        second_fetcher, tmp_path / "blobs", manifest_path=manifest
    )
    stored = second.store(
        repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON
    )
    assert stored.content == SOURCE
    assert second_fetcher.calls == 0


def test_missing_blob_forces_a_refetch(tmp_path) -> None:
    manifest = tmp_path / "content_manifest.jsonl"
    first = ContentStore(CountingFetcher(), tmp_path / "blobs")
    stored = first.store(
        repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON
    )
    first.write_manifest(manifest)
    (tmp_path / "blobs" / stored.record.blob_path).unlink()

    fetcher = CountingFetcher()
    second = ContentStore(fetcher, tmp_path / "blobs", manifest_path=manifest)
    second.store(repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON)
    assert fetcher.calls == 1


def test_replay_is_commit_scoped_not_path_scoped(tmp_path) -> None:
    manifest = tmp_path / "content_manifest.jsonl"
    first = ContentStore(CountingFetcher(), tmp_path / "blobs")
    first.store(repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON)
    first.write_manifest(manifest)

    fetcher = CountingFetcher()
    second = ContentStore(fetcher, tmp_path / "blobs", manifest_path=manifest)
    second.store(
        repo=REPO, path="a.py", commit_sha="f" * 40, language=Language.PYTHON
    )
    assert fetcher.calls == 1, "a different commit must be fetched, not reused"


def test_corrupt_manifest_is_rejected(tmp_path) -> None:
    manifest = tmp_path / "content_manifest.jsonl"
    manifest.write_text('{"file_id": "broken"}\n', encoding="utf-8")
    with pytest.raises(InvalidRecordError):
        ContentStore(CountingFetcher(), tmp_path / "blobs", manifest_path=manifest)


def test_hash_matches_the_blob_bytes(tmp_path) -> None:
    store = ContentStore(CountingFetcher(), tmp_path / "blobs")
    stored = store.store(
        repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON
    )
    blob = tmp_path / "blobs" / stored.record.blob_path
    assert hashlib.sha256(blob.read_bytes()).hexdigest() == stored.record.content_hash
