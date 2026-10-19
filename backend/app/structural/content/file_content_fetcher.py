"""File content at a commit.

Two modes:

* ``GitHubContentFetcher`` fetches ``raw.githubusercontent.com`` content at
  a specific commit SHA — never a branch, never ``main``.
* ``LocalContentFetcher`` reads a checked-out worktree or a pre-populated
  content directory, which is what tests and offline replays use.

Content is cached on disk keyed by ``repo/path@sha`` so a replay is
deterministic and does not depend on network availability.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.core.errors import InvalidRecordError, TransientUpstreamError


class ContentFetcher(Protocol):
    def fetch(self, *, repo: str, path: str, commit_sha: str) -> str: ...


class CachedContentFetcher:
    def __init__(self, inner: ContentFetcher, cache_dir: str | Path) -> None:
        self.inner = inner
        self.cache_dir = Path(cache_dir)

    def fetch(self, *, repo: str, path: str, commit_sha: str) -> str:
        target = self._cache_path(repo, path, commit_sha)
        if target.is_file():
            return target.read_text(encoding="utf-8")
        content = self.inner.fetch(repo=repo, path=path, commit_sha=commit_sha)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return content

    def _cache_path(self, repo: str, path: str, commit_sha: str) -> Path:
        digest = hashlib.sha256(f"{repo}\x00{path}\x00{commit_sha}".encode()).hexdigest()
        return self.cache_dir / repo.replace("/", "__") / f"{digest}.txt"


class LocalContentFetcher:
    """Reads ``<root>/<path>`` from a checkout of the target commit."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def fetch(self, *, repo: str, path: str, commit_sha: str) -> str:
        _ = repo, commit_sha
        target = self.root / path
        if not target.is_file():
            raise InvalidRecordError(
                f"content not found for {path}", component="structural"
            )
        return target.read_text(encoding="utf-8")


class GitHubContentFetcher:
    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        raw_root: str = "https://raw.githubusercontent.com",
    ) -> None:
        self.raw_root = raw_root.rstrip("/")
        self._client = client or httpx.Client(timeout=30.0, follow_redirects=True)

    def close(self) -> None:
        self._client.close()

    def fetch(self, *, repo: str, path: str, commit_sha: str) -> str:
        url = f"{self.raw_root}/{repo}/{commit_sha}/{quote(path)}"
        try:
            response = self._client.get(url)
        except httpx.HTTPError as error:
            raise TransientUpstreamError(
                f"failed to fetch {path}@{commit_sha}: {error}", component="structural"
            ) from error
        if response.status_code == 404:
            raise InvalidRecordError(
                f"{path} does not exist at {commit_sha}", component="structural"
            )
        if response.status_code >= 500:
            raise TransientUpstreamError(
                f"GitHub returned {response.status_code} for {path}@{commit_sha}",
                component="structural",
            )
        if response.status_code >= 400:
            raise InvalidRecordError(
                f"GitHub returned {response.status_code} for {path}@{commit_sha}",
                component="structural",
            )
        return response.text

    @staticmethod
    def blob_url(*, repo: str, path: str, commit_sha: str) -> str:
        return f"https://github.com/{repo}/blob/{commit_sha}/{path}"

    @staticmethod
    def raw_url(*, repo: str, path: str, commit_sha: str) -> str:
        return f"https://raw.githubusercontent.com/{repo}/{commit_sha}/{path}"


class RecordContentFetcher:
    """Content supplied inline by Component 1 (doc_file records, fixtures).

    This is the offline default: if the exported corpus already carries
    content, no network call happens at all.
    """

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._content: dict[tuple[str, str, str], str] = {}
        for record in records:
            if record.get("record_type") != "doc_file":
                continue
            payload = record.get("payload") or {}
            path = payload.get("path")
            content = payload.get("content")
            repo = record.get("repo")
            sha = payload.get("source_sha") or ""
            if isinstance(path, str) and isinstance(content, str) and isinstance(repo, str):
                self._content[(repo, path, str(sha))] = content
                self._content[(repo, path, "")] = content

    def fetch(self, *, repo: str, path: str, commit_sha: str) -> str:
        for key in ((repo, path, commit_sha), (repo, path, "")):
            if key in self._content:
                return self._content[key]
        raise InvalidRecordError(
            f"no inline content for {path}@{commit_sha}", component="structural"
        )


def write_manifest(entries: list[dict[str, Any]], path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out
