"""CLI contracts: `python -m app.ingestion.cli ingest` wires Settings,
store, client, service, and exporter into the artifact outputs the
plan's acceptance test names. The client factory is a module-level seam
so tests inject the fake instead of hitting GitHub."""

import json
from dataclasses import dataclass

import pytest
from fakes import FakeGitHubClient, all_prs

REPO = "encode/httpx"
INGEST_ARGS = ["ingest", "--repo", REPO, "--since", "2024-01-01", "--limit", "5"]


@pytest.fixture()
def artifacts_dir(tmp_path, monkeypatch):

    monkeypatch.setenv("WHYCODE_GITHUB_TOKEN", "gh_test")
    monkeypatch.setenv("WHYCODE_NEO4J_URI", "neo4j+s://0000.databases.neo4j.io")
    monkeypatch.setenv("WHYCODE_NEO4J_USER", "neo4j")
    monkeypatch.setenv("WHYCODE_NEO4J_PASSWORD", "not-real")
    monkeypatch.setenv("WHYCODE_LLM_API_KEY", "not-real")
    monkeypatch.setenv("WHYCODE_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    return tmp_path / "artifacts"


@pytest.fixture()
def inject_fake(monkeypatch):
    """Patch the client factory seam so the CLI never touches network."""
    from app.ingestion import cli

    injected: dict = {}

    def factory(token: str) -> FakeGitHubClient:
        injected["called"] = True
        return FakeGitHubClient(all_prs())

    monkeypatch.setattr(cli, "build_client", factory)
    return injected


@dataclass
class Exit:
    code: int
    stdout: str


def run_cli(args: list[str]) -> Exit:
    import contextlib
    import io

    from app.ingestion import cli

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = cli.main(args)
    return Exit(code=code, stdout=out.getvalue())


class TestIngestCommand:
    def test_ingest_produces_all_plan_artifacts(self, artifacts_dir, inject_fake, capsys) -> None:
        result = run_cli(INGEST_ARGS)
        assert result.code == 0
        assert (artifacts_dir / "evidence.db").exists()
        assert (artifacts_dir / "normalized.jsonl").exists()
        assert (artifacts_dir / "high_signal_prs.jsonl").exists()
        assert (artifacts_dir / "ingestion_report.json").exists()

    def test_ingestion_report_is_valid_json_with_counts(self, artifacts_dir, inject_fake) -> None:
        run_cli(INGEST_ARGS)
        report = json.loads((artifacts_dir / "ingestion_report.json").read_text())
        assert report["repo"] == REPO
        assert report["prs_total"] == 3
        assert report["prs_complete"] == 2
        assert report["prs_errored"] == 1

    def test_normalized_jsonl_roundtrips_the_records(self, artifacts_dir, inject_fake) -> None:
        run_cli(INGEST_ARGS)
        lines = (artifacts_dir / "normalized.jsonl").read_text().splitlines()
        ids = {json.loads(line)["record_id"] for line in lines}
        assert "httpx:pr:1131" in ids
        assert "httpx:review_comment:456" in ids

    def test_second_run_makes_no_new_artifacts_and_stays_idempotent(
        self, artifacts_dir, inject_fake
    ) -> None:
        run_cli(INGEST_ARGS)
        first = (artifacts_dir / "normalized.jsonl").read_text()
        run_cli(INGEST_ARGS)
        second = (artifacts_dir / "normalized.jsonl").read_text()
        assert first == second

    def test_bad_since_date_fails_fast_before_any_fetch(self, artifacts_dir, inject_fake) -> None:
        result = run_cli(["ingest", "--repo", REPO, "--since", "not-a-date"])
        assert result.code != 0
        assert "called" not in inject_fake, "must validate args before network"
