"""Pipeline + CLI contracts: dry-run by default, honest skips."""

import json

from structural_helpers import COMMIT, REPO, FakeSession, corpus, fixture_text

from app.structural.cli import main
from app.structural.config import StructuralConfig
from app.structural.content.file_content_fetcher import (
    CachedContentFetcher,
    LocalContentFetcher,
    RecordContentFetcher,
)
from app.structural.graph.neo4j_writer import StructuralGraphWriter
from app.structural.pipeline import StructuralPipeline
from app.structural.selection.file_selector import FileSelector


class FixtureFetcher:
    def fetch(self, *, repo, path, commit_sha):
        mapping = {
            "httpx/_client.py": "sample_client.py",
            "httpx/_config.py": "sample_config.yaml",
            "docs/retries.md": "sample_doc.md",
        }
        if path not in mapping:
            raise FileNotFoundError(path)
        return fixture_text(mapping[path])


def run_pipeline(records=None, *, writer=None, fetcher=None):
    records = records if records is not None else corpus(
        paths=("httpx/_client.py", "httpx/_config.py", "docs/retries.md"),
        doc_contents={"docs/retries.md": fixture_text("sample_doc.md")},
    )
    config = StructuralConfig(repo=REPO, commit_sha=COMMIT)
    selection = FileSelector(config).select(
        normalized_records=records, high_signal_prs=[{"pr_number": 1131}]
    )
    pipeline = StructuralPipeline(
        config=config, fetcher=fetcher or FixtureFetcher(), writer=writer
    )
    return pipeline.run(selection)


class TestPipeline:
    def test_end_to_end_extracts_files_symbols_and_regions(self) -> None:
        result = run_pipeline()
        assert len(result.batch.files) == 2
        assert result.validation.ok, result.validation.errors
        assert result.graph.counts()["nodes"]["Symbol"] > 0

    def test_docs_are_linked_to_the_file_they_name(self) -> None:
        result = run_pipeline()
        edges = [(edge.rel_type, edge.to_id) for edge in result.graph.edges]
        assert any(
            rel == "DOCUMENTED_BY" and target == "httpx:doc:docs/retries.md"
            for rel, target in edges
        )

    def test_no_writer_means_no_writes(self) -> None:
        session = FakeSession()
        result = run_pipeline()
        assert session.executed == 0
        assert result.write_report is None

    def test_writer_is_only_called_for_valid_graphs(self) -> None:
        session = FakeSession()
        result = run_pipeline(writer=StructuralGraphWriter(session))
        assert result.validation.ok
        assert session.executed > 0

    def test_missing_content_is_skipped_not_fabricated(self) -> None:
        records = corpus(paths=("httpx/_client.py", "httpx/_absent.py"))
        result = run_pipeline(records)
        assert {entry.path for entry in result.batch.files} == {"httpx/_client.py"}
        assert any(item["path"] == "httpx/_absent.py" for item in result.skipped)

    def test_inline_doc_content_needs_no_network(self) -> None:
        records = corpus(
            paths=("docs/retries.md",),
            doc_contents={"docs/retries.md": fixture_text("sample_doc.md")},
        )
        fetcher = RecordContentFetcher(records)
        assert "Retries" in fetcher.fetch(
            repo=REPO, path="docs/retries.md", commit_sha=COMMIT
        )


class TestCachedFetcher:
    def test_cache_avoids_a_second_fetch(self, tmp_path) -> None:
        calls = []

        class Counting:
            def fetch(self, *, repo, path, commit_sha):
                calls.append(path)
                return "content"

        cached = CachedContentFetcher(Counting(), tmp_path)
        first = cached.fetch(repo=REPO, path="a.py", commit_sha=COMMIT)
        second = cached.fetch(repo=REPO, path="a.py", commit_sha=COMMIT)
        assert first == second == "content"
        assert calls == ["a.py"]


class TestLocalFetcher:
    def test_reads_from_a_checkout_root(self, tmp_path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
        fetcher = LocalContentFetcher(tmp_path)
        assert fetcher.fetch(repo=REPO, path="pkg/a.py", commit_sha=COMMIT) == "x = 1\n"


class TestCli:
    def test_select_writes_a_manifest(self, tmp_path) -> None:
        normalized = tmp_path / "normalized.jsonl"
        normalized.write_text(
            "\n".join(json.dumps(record) for record in corpus()) + "\n", encoding="utf-8"
        )
        out = tmp_path / "selection.json"
        code = main(
            [
                "select",
                "--repo",
                REPO,
                "--normalized",
                str(normalized),
                "--commit",
                COMMIT,
                "--out",
                str(out),
            ]
        )
        assert code == 0
        payload = json.loads(out.read_text())
        assert payload["files"]

    def test_bad_input_returns_nonzero(self, tmp_path) -> None:
        assert (
            main(
                [
                    "select",
                    "--repo",
                    REPO,
                    "--normalized",
                    str(tmp_path / "missing.jsonl"),
                ]
            )
            == 1
        )

    def test_extract_writes_a_validatable_batch(self, tmp_path) -> None:
        normalized = tmp_path / "normalized.jsonl"
        normalized.write_text(
            "\n".join(
                json.dumps(record)
                for record in corpus(
                    paths=("docs/retries.md",),
                    doc_contents={"docs/retries.md": fixture_text("sample_doc.md")},
                )
            )
            + "\n",
            encoding="utf-8",
        )
        out = tmp_path / "batch.json"
        code = main(
            [
                "extract",
                "--repo",
                REPO,
                "--normalized",
                str(normalized),
                "--commit",
                COMMIT,
                "--out",
                str(out),
            ]
        )
        assert code == 0
        assert "files" in json.loads(out.read_text())

        validate_code = main(["validate", "--batch", str(out)])
        assert validate_code == 0
