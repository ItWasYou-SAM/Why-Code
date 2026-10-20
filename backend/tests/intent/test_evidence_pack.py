"""Phase 2 evidence-pack contracts.

These tests pin the properties that make the pack auditable: the documented
section order, region current/previous excerpts, evidence aliases that resolve
back to exact record IDs, category budgets, and the exclusion rules for test
files, CI, READMEs and oversized comment threads.
"""

from __future__ import annotations

from typing import Any

from intent_helpers import REPO, fixture

from app.domain.records import EvidenceRecord
from app.intent.context.budget import CATEGORY_SPECS, allocate_budget
from app.intent.context.context_packer import ContextPacker
from app.intent.context.prompt_render import ExtractionKind, render_prompt
from app.intent.models import TargetOption, TargetType
from app.intent.selection.candidate_detector import CandidateDetector
from app.intent.selection.evidence_selector import EvidenceSelector


def _row(
    record_id: str,
    record_type: str,
    payload: dict[str, Any],
    *,
    pr_number: int = 1419,
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "record_type": record_type,
        "repo": REPO,
        "pr_number": pr_number,
        "payload": payload,
        "content_hash": "0" * 64,
        "ingested_at": "2026-10-19T10:00:00+00:00",
    }


def bundle_with(*extra: dict[str, Any]):
    rows = [*fixture("retry_pr_evidence.json"), *extra]
    selected = EvidenceSelector(REPO).select(
        [EvidenceRecord.model_validate(row) for row in rows]
    )
    return selected[0]


def candidate_for(bundle):
    return CandidateDetector().detect(bundle)[0]


def changed_file(path: str, patch: str, *, pr_number: int = 1419) -> dict[str, Any]:
    return _row(
        f"httpx:pr:{pr_number}:file:{path}",
        "changed_file",
        {
            "path": path,
            "previous_filename": None,
            "status": "modified",
            "additions": 1,
            "deletions": 0,
            "patch": patch,
        },
        pr_number=pr_number,
    )


def review_comment(
    record_id: str,
    path: str,
    line: int,
    body: str = "This needs to change because the current behaviour is unsafe.",
) -> dict[str, Any]:
    return _row(
        record_id,
        "review_comment",
        {
            "path": path,
            "line": line,
            "start_line": line,
            "side": "RIGHT",
            "body": body,
            "author": "reviewer",
            "created_at": "2024-03-30T09:05:00Z",
            "url": "",
            "pull_request_review_id": 2001,
        },
    )


def commit(sha: str, message: str, *, at: str) -> dict[str, Any]:
    return _row(
        f"httpx:commit:{sha}",
        "commit",
        {
            "sha": sha,
            "message": message,
            "author": "dev",
            "committed_at": at,
        },
    )


def issue_comment(record_id: str, body: str, *, at: str) -> dict[str, Any]:
    return _row(
        record_id,
        "issue_comment",
        {
            "body": body,
            "author": "commenter",
            "created_at": at,
            "url": "",
            "issue_number": 1234,
        },
    )


class TestPackFormat:
    def test_documented_sections_are_present(self) -> None:
        bundle = bundle_with()
        options = [
            TargetOption(
                type=TargetType.CONFIG_KEY,
                identifier="retries",
                graph_key="httpx:config:httpx/_config.py:retries",
                path="httpx/_config.py",
            )
        ]
        pack = ContextPacker().pack(
            bundle, candidate_for(bundle), target_options=options
        )
        text = pack.text()
        for marker in (
            "TASK:",
            "REPOSITORY: encode/httpx",
            "PULL_REQUEST: #1419",
            "TITLE:",
            "MERGED_AT:",
            "TARGETS",
            "TARGET_CODE_REGION:",
            "Current code:",
            "Previous code:",
            "DIFF:",
            "EVIDENCE",
            "RULES:",
        ):
            assert marker in text

    def test_region_reconstructs_both_sides_of_the_hunk(self) -> None:
        bundle = bundle_with()
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        text = pack.text()
        assert "104 |                 if self.retries:" in text
        assert "104 |                 raise" in text
        assert "@@ -100,6 +100,12 @@" in text

    def test_pure_addition_has_no_previous_code(self) -> None:
        bundle = bundle_with(
            changed_file("new.py", "@@ -0,0 +1,2 @@\n+line one\n+line two")
        )
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        section = next(
            item for item in pack.sections if "new.py" in item.title
        )
        assert "Current code:" in section.text
        assert "line one" in section.text
        assert "Previous code: (no previous lines in this hunk)" in section.text

    def test_prompt_contains_the_same_aliases(self) -> None:
        bundle = bundle_with()
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        system, user = render_prompt(ExtractionKind.DECISION, pack)
        assert "EVIDENCE section" in system
        assert "Do not infer intent from code style alone" in system
        assert "[E1]" in user
        assert "RULES:" in user


class TestAliases:
    def test_alias_map_points_at_exact_record_ids(self) -> None:
        bundle = bundle_with()
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        assert pack.alias_map
        assert set(pack.alias_map.values()) == set(pack.evidence_ids())
        for alias, evidence_id in pack.alias_map.items():
            assert alias.startswith("E")
            assert evidence_id in bundle.evidence_ids()
            assert f"[{alias}]" in pack.text()
            assert evidence_id in pack.text()

    def test_citations_resolve_from_labels_and_record_ids(self) -> None:
        bundle = bundle_with()
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        alias = next(iter(pack.alias_map))
        record_id = pack.alias_map[alias]
        assert pack.resolve_citation(alias) == record_id
        assert pack.resolve_citation(f"[{alias}]") == record_id
        assert pack.resolve_citation(alias.lower()) == record_id
        assert pack.resolve_citation(record_id) == record_id

    def test_omitted_evidence_has_no_alias(self) -> None:
        bundle = bundle_with()
        packer = ContextPacker(max_tokens=300)
        pack = packer.pack(bundle, candidate_for(bundle))
        assert pack.omitted_ids
        assert not set(pack.alias_map.values()) & set(pack.omitted_ids)
        assert not set(pack.omitted_ids) & set(pack.evidence_ids())


class TestPriorityAndBudget:
    def test_sections_follow_the_evidence_priority(self) -> None:
        bundle = bundle_with()
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        categories = [section.category for section in pack.sections]
        order = [
            "diff",
            "review_comments",
            "commit_messages",
            "pr_description",
            "issue_context",
            "docs",
        ]
        positions = [categories.index(category) for category in order]
        assert positions == sorted(positions)

    def test_tight_budget_keeps_diff_comments_and_commits(self) -> None:
        bundle = bundle_with()
        pack = ContextPacker(max_tokens=900).pack(
            bundle, candidate_for(bundle)
        )
        ids = set(pack.evidence_ids())
        assert "httpx:pr:1419:file:httpx/_client.py" in ids
        assert "httpx:review_comment:123" in ids
        assert "httpx:commit:" + "a" * 40 in ids
        assert pack.estimated_tokens <= 900

    def test_allocator_honours_floors_and_ceilings(self) -> None:
        present = {spec.name for spec in CATEGORY_SPECS}
        allocation = allocate_budget(6000, present)
        assert sum(allocation.values()) <= 6000
        for spec in CATEGORY_SPECS:
            assert spec.min_tokens <= allocation[spec.name] <= spec.max_tokens

    def test_allocator_degrades_gracefully_below_the_floors(self) -> None:
        allocation = allocate_budget(250, {spec.name for spec in CATEGORY_SPECS})
        assert sum(allocation.values()) <= 250
        assert all(value >= 0 for value in allocation.values())


class TestExclusions:
    def test_unrelated_test_files_are_not_packed(self) -> None:
        bundle = bundle_with(
            changed_file(
                "tests/test_client.py",
                "@@ -1,2 +1,3 @@\n def test_a():\n-    assert True\n+    assert 1\n+    assert 2",
            )
        )
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        assert "httpx:pr:1419:file:tests/test_client.py" not in pack.evidence_ids()
        assert "tests/test_client.py" not in pack.text()

    def test_ci_files_are_not_packed(self) -> None:
        bundle = bundle_with(
            changed_file(
                ".github/workflows/ci.yml",
                "@@ -1,1 +1,2 @@\n name: ci\n+on: push",
            )
        )
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        assert ".github/workflows/ci.yml" not in pack.text()

    def test_readme_is_excluded_when_not_directly_linked(self) -> None:
        bundle = bundle_with(
            changed_file("README.md", "@@ -1,1 +1,2 @@\n # Why-Code\n+More docs.")
        )
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        assert "README.md" not in pack.text()

    def test_readme_is_kept_when_a_review_comment_links_it(self) -> None:
        bundle = bundle_with(
            changed_file("README.md", "@@ -1,1 +1,2 @@\n # Why-Code\n+More docs."),
            review_comment("httpx:review_comment:readme", "README.md", 1),
        )
        pack = ContextPacker().pack(bundle, candidate_for(bundle))
        assert "README.md" in pack.text()
        assert "httpx:review_comment:readme" in pack.evidence_ids()


class TestThreadAndCommitCaps:
    def test_large_commit_lists_are_capped(self) -> None:
        extras = [
            commit(
                f"{index:040x}",
                f"change {index} because it matters",
                at=f"2024-04-0{index}T10:00:00Z",
            )
            for index in range(1, 6)
        ]
        bundle = bundle_with(*extras)
        pack = ContextPacker(max_commit_messages=3).pack(
            bundle, candidate_for(bundle)
        )
        commit_ids = [
            evidence_id
            for evidence_id in pack.evidence_ids()
            if evidence_id.startswith("httpx:commit:")
        ]
        assert len(commit_ids) == 3

    def test_issue_threads_are_summarised_and_keep_their_ids(self) -> None:
        extras = [
            issue_comment(
                f"httpx:issue_comment:{9100 + index}",
                "This matters because the regression duplicated writes.",
                at=f"2024-03-2{index}T12:00:00Z",
            )
            for index in range(1, 6)
        ]
        bundle = bundle_with(*extras)
        pack = ContextPacker(max_issue_comments=4).pack(
            bundle, candidate_for(bundle)
        )
        comment_ids = [
            evidence_id
            for evidence_id in pack.evidence_ids()
            if evidence_id.startswith("httpx:issue_comment:")
        ]
        assert len(comment_ids) == 4
        assert "lower-signal comment(s) omitted from this thread" in pack.text()
        omitted_comments = [
            evidence_id
            for evidence_id in pack.omitted_ids
            if evidence_id.startswith("httpx:issue_comment:")
        ]
        assert len(omitted_comments) == 2
