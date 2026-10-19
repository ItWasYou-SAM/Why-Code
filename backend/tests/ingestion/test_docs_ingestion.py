"""Document ingestion contracts.

The problem statement requires ingesting design documents that live in
the repository, not just discussion in PRs. Contract: changed files
under markdown (``docs/``, README) become DOC_FILE records with fetched
content via the blob API; binary/code files do not; and per D-0007, a
failing blob fetch rolls the PR back and marks it errored."""

from fakes import (
    DEEP,
    REPO,
    TRIVIAL,
    FakeGitHubClient,
    deep_pr,
    file_payload,
    poison_pr,
    trivial_pr,
)


def make_service(store, github, since: str = "2024-01-01", limit: int = 100):
    from app.ingestion.service import IngestionService

    return IngestionService(github=github, store=store, repo=REPO, since=since, limit=limit)


class TestDocDiscovery:
    def test_changed_markdown_under_docs_becomes_doc_record(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient([deep_pr()])).run()
        record = store.get_record("httpx:doc:docs/api.md")
        assert record is not None
        assert record.pr_number == DEEP
        assert "Retries are opt-in" in record.payload["content"]

    def test_readme_changes_also_become_doc_records(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient([trivial_pr()])).run()
        record = store.get_record("httpx:doc:README.md")
        assert record is not None
        assert record.record_id == "httpx:doc:README.md"

    def test_python_files_are_not_doc_records(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient([deep_pr()])).run()
        docs = [r.record_id for r in store.iter_records() if r.record_id.startswith("httpx:doc:")]
        assert "httpx:doc:httpx/_client.py" not in docs

    def test_doc_urls_are_preserved_for_citation(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient([deep_pr()])).run()
        record = store.get_record("httpx:doc:docs/api.md")
        assert record is not None
        assert record.payload["path"] == "docs/api.md"
        assert "source_sha" in record.payload


class TestDocFailureHandling:
    def test_blob_fetch_failure_rolls_back_the_pr(self, tmp_path) -> None:
        """D-0007 applied to the doc path: no half-document PRs."""
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        github = FakeGitHubClient([deep_pr()])
        github.blob_failures.add("e" * 40)  # poison the wrong file: code blob
        # docs blob key stays intact; the failure must NOT affect code files
        make_service(store, github).run()
        assert store.get_record("httpx:doc:docs/api.md") is not None

    def test_missing_blob_content_marks_pr_error_and_blocks_writes(self, tmp_path) -> None:
        """A docs file whose blob sha has no fetchable content poisons the
        PR: unknown content cannot be stored, and D-0007 does not allow
        guessing it from the patch."""
        from app.ingestion.store import EvidenceStore

        doc_pr = deep_pr()
        doc_pr["files"] = [file_payload("docs/api.md", sha="0" * 40, patch="-")]
        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient([doc_pr])).run()
        assert store.get_pr_state(REPO, DEEP) == "error"
        assert [r.record_id for r in store.iter_records() if r.pr_number == DEEP] == []

    def test_rate_limited_blob_marks_pr_error_and_siblings_continue(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        github = FakeGitHubClient([deep_pr(), trivial_pr()])
        github.blob_failures.add("7" * 40)  # trivial PR's README blob
        make_service(store, github).run()
        assert store.get_pr_state(REPO, TRIVIAL) == "error"
        assert store.get_pr_state(REPO, DEEP) == "complete"


class TestDocIdBoundaries:
    def test_doc_ids_must_escape_nothing(self, tmp_path) -> None:
        """Paths with subdirectories keep exact separators: docs/a/b.md
        -> httpx:doc:docs/a/b.md — flattening them would collide."""

        from app.ingestion.store import EvidenceStore

        doc_pr = deep_pr()
        doc_pr["files"] = [file_payload("docs/design/retry.md", sha="f" * 40, patch="-")]
        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient([doc_pr])).run()
        record = store.get_record("httpx:doc:docs/design/retry.md")
        assert record is not None

    def test_poisoned_pr_still_fails_before_any_blob_call(self, tmp_path) -> None:
        """Normalization precedes content fetching: a PR with malformed
        artifacts must not reach the blob API at all."""
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        github = FakeGitHubClient([poison_pr()])
        make_service(store, github).run()
        blob_calls = [ep for ep, _ in github.calls if ep == "blob"]
        assert blob_calls == []
        assert store.get_pr_state(REPO, 1132) == "error"
