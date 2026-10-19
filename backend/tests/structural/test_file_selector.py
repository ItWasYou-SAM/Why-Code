"""File selection contracts: only high-signal, only path@commit."""

from structural_helpers import COMMIT, REPO, corpus, high_signal

from app.structural.config import StructuralConfig
from app.structural.extraction.models import Language
from app.structural.selection.file_selector import FileSelector


def select(records, *, high=None, **overrides):
    config = StructuralConfig(repo=REPO, **overrides)
    return FileSelector(config).select(
        normalized_records=records, high_signal_prs=high if high is not None else high_signal()
    )


class TestSelection:
    def test_changed_python_files_are_selected(self) -> None:
        result = select(corpus())
        paths = {entry.path for entry in result.files}
        assert "httpx/_client.py" in paths
        assert "pyproject.toml" in paths

    def test_selection_is_path_at_commit(self) -> None:
        result = select(corpus())
        client = next(entry for entry in result.files if entry.path == "httpx/_client.py")
        assert client.commit_sha == COMMIT
        assert client.reason == "changed_in_high_signal_pr"

    def test_files_from_low_signal_prs_are_excluded(self) -> None:
        result = select(corpus(), high=[])
        assert result.files == []

    def test_tests_are_excluded_by_default(self) -> None:
        records = corpus(paths=("httpx/_client.py", "tests/test_client.py"))
        paths = {entry.path for entry in select(records).files}
        assert "tests/test_client.py" not in paths

    def test_tests_can_be_included(self) -> None:
        records = corpus(paths=("tests/test_client.py",))
        paths = {entry.path for entry in select(records, include_tests=True).files}
        assert paths == {"tests/test_client.py"}

    def test_lock_files_and_unsupported_languages_are_skipped_with_reasons(self) -> None:
        records = corpus(paths=("uv.lock", "image.png", "httpx/_client.py"))
        result = select(records)
        reasons = {item["path"]: item["reason"] for item in result.skipped}
        assert reasons["uv.lock"] == "lock_file"
        assert reasons["image.png"] == "unsupported_language"

    def test_missing_commit_is_skipped_not_guessed(self) -> None:
        records = [r for r in corpus() if r["record_type"] != "commit"]
        result = select(records)
        assert result.files == []
        assert {item["reason"] for item in result.skipped} == {"no_merge_commit_for_pr"}

    def test_pinned_file_is_selected_at_the_pinned_commit(self) -> None:
        result = select(
            corpus(paths=()), commit_sha=COMMIT, pinned_paths=["httpx/_client.py"]
        )
        assert [(entry.path, entry.reason) for entry in result.files] == [
            ("httpx/_client.py", "manually_pinned")
        ]

    def test_language_is_assigned_from_suffix(self) -> None:
        result = select(corpus(paths=("a.py", "b.yaml", "c.toml", "d.md")))
        languages = {entry.path: entry.language for entry in result.files}
        assert languages["a.py"] is Language.PYTHON
        assert languages["b.yaml"] is Language.YAML
        assert languages["c.toml"] is Language.TOML
        assert languages["d.md"] is Language.MARKDOWN

    def test_selection_is_deterministic(self) -> None:
        first = select(corpus())
        second = select(corpus())
        assert [entry.as_dict() for entry in first.files] == [
            entry.as_dict() for entry in second.files
        ]

    def test_multiple_prs_touching_the_same_file_keep_both_pr_numbers(self) -> None:
        records = corpus() + corpus(pr_number=1132, commit_sha="b" * 40)
        result = select(records, high=[{"pr_number": 1131}, {"pr_number": 1132}])
        clients = [entry for entry in result.files if entry.path == "httpx/_client.py"]
        assert {entry.commit_sha for entry in clients} == {COMMIT, "b" * 40}
