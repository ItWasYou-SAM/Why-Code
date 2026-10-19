"""Phase 2 contracts: fetch content at the merge commit, store off-graph.

Two rules dominate these tests: content is always read at the commit (never a
branch tip), and the full text lives in artifact storage while the graph
keeps only small metadata.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from structural_helpers import COMMIT, REPO, SHORT

from app.core.errors import InvalidRecordError, TransientUpstreamError
from app.structural.config import StructuralConfig
from app.structural.content.content_store import ContentRecord, ContentStore
from app.structural.content.file_content_fetcher import GitHubContentFetcher
from app.structural.extraction.models import Language
from app.structural.pipeline import StructuralPipeline
from app.structural.selection.file_selector import SelectedFile, SelectionResult

CLIENT_SOURCE = "import os\n\n\nclass Client:\n    pass\n"


def _selected_file(path: str) -> SelectedFile:
    return SelectedFile(
        repo=REPO,
        commit_sha=COMMIT,
        path=path,
        language=Language.PYTHON,
        reason="test",
    )


class FakeResponse:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


class RecordingClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.urls: list[str] = []

    def get(self, url: str) -> FakeResponse:
        self.urls.append(url)
        return self.response


class DictFetcher:
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping
        self.calls: list[tuple[str, str]] = []

    def fetch(self, *, repo: str, path: str, commit_sha: str) -> str:
        self.calls.append((path, commit_sha))
        if path not in self.mapping:
            raise InvalidRecordError(f"no content for {path}", component="structural")
        return self.mapping[path]


class TestGitHubContentFetcher:
    def test_reads_at_the_commit_never_a_branch(self) -> None:
        client = RecordingClient(FakeResponse(200, CLIENT_SOURCE))
        fetcher = GitHubContentFetcher(client=client)  # type: ignore[arg-type]
        assert fetcher.fetch(repo=REPO, path="httpx/_client.py", commit_sha=COMMIT) == (
            CLIENT_SOURCE
        )
        url = client.urls[0]
        assert url == f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/httpx/_client.py"
        for branch in ("master", "main", "HEAD", "refs/heads"):
            assert branch not in url

    def test_missing_file_is_an_invalid_record(self) -> None:
        fetcher = GitHubContentFetcher(
            client=RecordingClient(FakeResponse(404))  # type: ignore[arg-type]
        )
        with pytest.raises(InvalidRecordError):
            fetcher.fetch(repo=REPO, path="gone.py", commit_sha=COMMIT)

    def test_server_error_is_transient(self) -> None:
        fetcher = GitHubContentFetcher(
            client=RecordingClient(FakeResponse(503))  # type: ignore[arg-type]
        )
        with pytest.raises(TransientUpstreamError):
            fetcher.fetch(repo=REPO, path="a.py", commit_sha=COMMIT)

    def test_other_client_error_is_invalid(self) -> None:
        fetcher = GitHubContentFetcher(
            client=RecordingClient(FakeResponse(403))  # type: ignore[arg-type]
        )
        with pytest.raises(InvalidRecordError):
            fetcher.fetch(repo=REPO, path="a.py", commit_sha=COMMIT)


class TestContentStore:
    def test_record_carries_identity_hash_size_and_lines(self, tmp_path) -> None:
        store = ContentStore(DictFetcher({"a.py": CLIENT_SOURCE}), tmp_path)
        stored = store.store(
            repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON
        )
        record = stored.record
        assert stored.content == CLIENT_SOURCE
        assert record.file_id == f"{SHORT}:file:a.py@{COMMIT}"
        assert record.content_hash == hashlib.sha256(CLIENT_SOURCE.encode()).hexdigest()
        assert record.byte_size == len(CLIENT_SOURCE.encode("utf-8"))
        assert record.line_count == CLIENT_SOURCE.count("\n")

    def test_full_content_goes_to_a_content_addressed_blob(self, tmp_path) -> None:
        store = ContentStore(DictFetcher({"a.py": CLIENT_SOURCE}), tmp_path)
        stored = store.store(
            repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON
        )
        blob = tmp_path / stored.record.blob_path
        assert blob.read_text(encoding="utf-8") == CLIENT_SOURCE
        assert stored.record.blob_path.endswith(f"{stored.record.content_hash}.txt")

    def test_identical_content_is_written_once(self, tmp_path) -> None:
        fetcher = DictFetcher({"a.py": CLIENT_SOURCE, "b.py": CLIENT_SOURCE})
        store = ContentStore(fetcher, tmp_path)
        first = store.store(
            repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON
        )
        second = store.store(
            repo=REPO, path="b.py", commit_sha=COMMIT, language=Language.PYTHON
        )
        assert first.record.blob_path == second.record.blob_path
        assert len(list(tmp_path.rglob("*.txt"))) == 1

    def test_oversized_file_is_refused_before_storing(self, tmp_path) -> None:
        store = ContentStore(DictFetcher({"a.py": CLIENT_SOURCE}), tmp_path)
        with pytest.raises(InvalidRecordError):
            store.store(
                repo=REPO,
                path="a.py",
                commit_sha=COMMIT,
                language=Language.PYTHON,
                max_bytes=4,
            )
        assert store.records == []
        assert list(tmp_path.rglob("*.txt")) == []

    def test_manifest_has_metadata_but_never_content(self, tmp_path) -> None:
        store = ContentStore(DictFetcher({"a.py": CLIENT_SOURCE}), tmp_path)
        store.store(repo=REPO, path="a.py", commit_sha=COMMIT, language=Language.PYTHON)
        manifest = store.write_manifest(tmp_path / "content_manifest.jsonl")
        lines = manifest.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert "content" not in payload
        assert payload["file_id"] == f"{SHORT}:file:a.py@{COMMIT}"
        assert payload["blob_path"]
        ContentRecord.model_validate(payload)

    def test_manifest_is_sorted_and_complete(self, tmp_path) -> None:
        fetcher = DictFetcher({"b.py": "b = 2\n", "a.py": "a = 1\n", "c.py": "c = 3\n"})
        store = ContentStore(fetcher, tmp_path)
        for path in ("b.py", "c.py", "a.py"):
            store.store(repo=REPO, path=path, commit_sha=COMMIT, language=Language.PYTHON)
        manifest = store.write_manifest(tmp_path / "m.jsonl")
        paths = [json.loads(line)["path"] for line in manifest.read_text().splitlines()]
        assert paths == ["a.py", "b.py", "c.py"]


class TestPipelineContentMetadata:
    def test_file_node_carries_content_metadata(self, tmp_path) -> None:
        config = StructuralConfig(repo=REPO, commit_sha=COMMIT)
        selection = SelectionResult(files=[_selected_file("a.py")])
        fetcher = DictFetcher({"a.py": CLIENT_SOURCE})
        store = ContentStore(fetcher, tmp_path)
        result = StructuralPipeline(
            config=config, fetcher=fetcher, content_store=store
        ).run(selection)
        nodes = {node.node_id: node for node in result.graph.nodes}
        node = nodes[f"{SHORT}:file:a.py@{COMMIT}"]
        assert node.props["content_hash"] == hashlib.sha256(
            CLIENT_SOURCE.encode()
        ).hexdigest()
        assert node.props["byte_size"] == len(CLIENT_SOURCE.encode("utf-8"))
        assert node.props["line_count"] == CLIENT_SOURCE.count("\n")
        # The full text is never a node property.
        assert "content" not in node.props
        assert result.content and result.content[0].blob_path
