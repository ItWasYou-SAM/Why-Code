"""IngestionService orchestration contracts.

Covers: window filtering, skip-if-complete, resume, forced re-ingestion,
per-PR atomic rollback (D-0007), poisoned-PR run survival, rate-limit
survival, the limit parameter, and the artifact outputs of a run.
The service is driven through its public run() with the fake client and
a real SQLite store."""


from fakes import (
    DEEP,
    POISON,
    REPO,
    TRIVIAL,
    FakeGitHubClient,
    all_prs,
    deep_pr,
    poison_pr,
    trivial_pr,
)

from app.core.errors import RateLimitError


def make_service(store, github, since: str = "2024-01-01", limit: int = 100):
    from app.ingestion.service import IngestionService

    return IngestionService(github=github, store=store, repo=REPO, since=since, limit=limit)


class TestHappyPath:
    def test_window_filtering_excludes_early_prs(self, tmp_path, monkeypatch) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        early = poison_pr()
        early["merged_at"] = "2024-01-01T00:00:00Z"  # before window start 2024-04-01
        github = FakeGitHubClient([early, deep_pr()])
        svc = make_service(store, github, since="2024-04-01")
        svc.run()
        assert store.get_pr_state(REPO, POISON) == "pending"
        assert store.get_pr_state(REPO, DEEP) == "complete"

    def test_boundary_merged_exactly_on_since_is_included(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        on_boundary = trivial_pr()
        on_boundary["merged_at"] = "2024-04-01T00:00:00Z"
        github = FakeGitHubClient([on_boundary])
        make_service(store, github, since="2024-04-01").run()
        assert store.get_pr_state(REPO, TRIVIAL) == "complete"

    def test_run_ingests_every_artifact_of_the_pr(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient([deep_pr()])).run()
        ids = {r.record_id for r in store.iter_records()}
        for expected in (
            "httpx:pr:1131",
            "httpx:commit:a" + "a" * 39,
            "httpx:pr:1131:file:httpx/_client.py",
            "httpx:review:101",
            "httpx:review_comment:456",
            "httpx:issue_comment:789",
            "httpx:issue:108",
            "httpx:person:tomchristie",
        ):
            assert expected in ids, f"missing {expected}"

    def test_main_pr_survives_a_poisoned_sibling(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient(all_prs())).run()
        assert store.get_pr_state(REPO, DEEP) == "complete"
        assert store.get_pr_state(REPO, POISON) == "error"

    def test_poisoned_pr_leaves_zero_partial_writes(self, tmp_path) -> None:
        """D-0007: any artifact failure rolls back the entire PR."""
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient(all_prs())).run()
        poisoned_artifact_ids = [
            r.record_id for r in store.iter_records() if r.pr_number == POISON
        ]
        assert poisoned_artifact_ids == []
        assert store.get_pr_error(REPO, POISON) is not None

    def test_run_is_idempotent_and_makes_no_duplicate_calls(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        first = FakeGitHubClient(all_prs())
        make_service(store, first).run()
        calls_after_run_one = first.total_calls()
        count_after_run_one = store.count_records()
        rerun_client = FakeGitHubClient(all_prs())
        make_service(store, rerun_client).run()
        assert rerun_client.total_calls() == 0, "complete PRs must not be refetched"
        assert store.count_records() == count_after_run_one
        assert calls_after_run_one > 0

    def test_unmerged_prs_are_never_ingested(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        draft = trivial_pr()
        draft["merged"] = False
        github = FakeGitHubClient([draft])
        make_service(store, github, since="2024-04-01").run()
        assert store.count_records() == 0
        assert store.pr_numbers_with_status("complete") == []

class TestReingestSemantics:
    def test_force_refetches_completed_prs(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        make_service(store, FakeGitHubClient(all_prs())).run()
        forced_client = FakeGitHubClient(all_prs())
        svc = make_service(store, forced_client)
        svc.run(force=True)
        assert forced_client.calls_for_pr(DEEP) > 0
        assert store.get_pr_state(REPO, POISON) == "error"

    def test_resume_after_crash_only_ingests_the_missing_prs(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        store.set_pr_state(REPO, DEEP, "complete")
        resumed = FakeGitHubClient(all_prs())
        make_service(store, resumed).run()
        assert resumed.calls_for_pr(DEEP) == 0
        assert resumed.calls_for_pr(TRIVIAL) > 0
        assert store.get_pr_state(REPO, DEEP) == "complete"

    def test_limit_caps_the_number_of_prs_ingested(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        svc = make_service(store, FakeGitHubClient(all_prs()), limit=1)
        svc.run()
        completed = store.pr_numbers_with_status("complete")
        assert len(completed) <= 1


class TestFailureSurvival:
    def test_rate_limited_pr_is_marked_error_and_run_continues(self, tmp_path) -> None:
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        github = FakeGitHubClient(all_prs())

        # The real client raises RateLimitError after exhaustion; here the
        # service handles that behavior right on the boundary.
        real_files = FakeGitHubClient.pr_files

        def flaky_files(self, repo, number):
            if number == TRIVIAL:
                raise RateLimitError("rate limited", component="ingestion", record_id=None)
            return real_files(self, repo, number)

        FakeGitHubClient.pr_files = flaky_files
        try:
            make_service(store, github).run()
        finally:
            del FakeGitHubClient.pr_files
            FakeGitHubClient.pr_files = real_files
        assert store.get_pr_state(REPO, TRIVIAL) == "error"
        assert store.get_pr_state(REPO, DEEP) == "complete"


class TestArtifacts:
    def test_run_scores_prs_and_populates_the_report_inputs(self, tmp_path) -> None:
        from app.ingestion.service import IngestionService
        from app.ingestion.store import EvidenceStore

        store = EvidenceStore(tmp_path / "e.db")
        svc = IngestionService(
            github=FakeGitHubClient([deep_pr()]), store=store, repo=REPO, since="2024-01-01",
                limit=10
        )
        result = svc.run()
        assert result.scored[DEEP].band in {"low", "medium", "high"}
        assert result.scored[DEEP].score >= result.scored[POISON].score or POISON in result.errored
