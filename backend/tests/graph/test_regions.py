"""Phase 7 contracts: code regions come from explicit spans only."""

from __future__ import annotations

from graph_fixtures import (
    COMMIT_B,
    CORPUS,
    DEEP,
    DEEP_RECORDS,
    SHORT,
    SMALL_PR,
    record_by_id,
)

from app.graph.mapping import ops_from_records
from app.graph.regions import parse_patch_hunks


class TestUnifiedDiffParsing:
    def test_one_span_per_hunk(self) -> None:
        spans = parse_patch_hunks(
            "@@ -10,3 +10,4 @@ def client():\n"
            " line\n"
            "+added\n"
            "@@ -30 +31 @@\n"
            "-old\n"
            "+new\n"
        )
        assert [(span.start_line, span.end_line) for span in spans] == [
            (10, 13),
            (31, 31),
        ]
        assert spans[0].header == "@@ -10,3 +10,4 @@ def client():"

    def test_omitted_count_defaults_to_one(self) -> None:
        (span,) = parse_patch_hunks("@@ -5 +7 @@\n")
        assert (span.old_count, span.new_count) == (1, 1)
        assert (span.start_line, span.end_line) == (7, 7)

    def test_pure_deletion_anchors_at_the_deletion_line(self) -> None:
        (span,) = parse_patch_hunks("@@ -50,2 +50,0 @@\n-a\n-b\n")
        assert (span.start_line, span.end_line) == (50, 50)

    def test_empty_new_file_has_no_region(self) -> None:
        assert parse_patch_hunks("@@ -1,5 +0,0 @@\n-a\n") == []

    def test_missing_or_malformed_patch_is_empty(self) -> None:
        assert parse_patch_hunks(None) == []
        assert parse_patch_hunks("") == []
        assert parse_patch_hunks("not a diff\n") == []


class TestHunkRegions:
    def test_each_hunk_becomes_a_commit_scoped_region(self) -> None:
        patched = dict(record_by_id(f"{SHORT}:pr:{DEEP}:file:httpx/_client.py"))
        patched["payload"] = {
            **patched["payload"],
            "patch": "@@ -240,6 +240,9 @@\n context\n+added\n@@ -300,1 +303,2 @@\n+x\n+y\n",
        }
        records = [
            patched if record["record_id"] == patched["record_id"] else record
            for record in DEEP_RECORDS
        ]
        plan = ops_from_records(records)
        regions = {
            node.record_id: node
            for node in plan.nodes
            if node.label == "CodeRegion" and node.props.get("source") == "changed_hunk"
        }
        assert set(regions) == {
            f"{SHORT}:region:httpx/_client.py@{COMMIT_B}:240:248",
            f"{SHORT}:region:httpx/_client.py@{COMMIT_B}:303:304",
        }
        first = regions[f"{SHORT}:region:httpx/_client.py@{COMMIT_B}:240:248"]
        assert first.props["file_id"] == f"{SHORT}:file:httpx/_client.py@{COMMIT_B}"
        assert first.props["source_pr_number"] == DEEP
        assert first.props["content_hash"] == "deadbeef"
        assert first.props["start_line"] == 240
        assert first.props["end_line"] == 248

    def test_hunk_region_is_owned_and_changed_in_commit(self) -> None:
        plan = ops_from_records(CORPUS)
        changed_file = f"{SHORT}:pr:{DEEP}:file:httpx/_client.py"
        region = f"{SHORT}:region:httpx/_client.py@{COMMIT_B}:240:248"
        commit = f"{SHORT}:commit:{COMMIT_B}"
        rels = {
            (rel.rel_type, rel.from_record_id, rel.to_record_id)
            for rel in plan.rels
        }
        assert ("HAS_REGION", changed_file, region) in rels
        assert ("CHANGED_IN", region, commit) in rels

    def test_only_high_signal_pr_hunks_when_filtered(self) -> None:
        plan = ops_from_records(CORPUS, high_signal_pr_numbers={SMALL_PR})
        hunk_prs = {
            node.props["source_pr_number"]
            for node in plan.nodes
            if node.label == "CodeRegion"
            and node.props.get("source") == "changed_hunk"
        }
        assert hunk_prs == {SMALL_PR}

    def test_no_commit_means_no_hunk_region(self) -> None:
        changed = record_by_id(f"{SHORT}:pr:{DEEP}:file:httpx/_client.py")
        pr = record_by_id(f"{SHORT}:pr:{DEEP}")
        plan = ops_from_records([pr, changed])
        assert not [node for node in plan.nodes if node.label == "CodeRegion"]


class TestReviewRegions:
    def test_review_region_keeps_pinned_id_and_gains_bridge_fields(self) -> None:
        plan = ops_from_records(CORPUS)
        region = plan.node(f"{SHORT}:region:httpx/_client.py:240:243")
        assert region is not None
        assert region.props["source"] == "review_comment"
        assert region.props["source_pr_number"] == DEEP
        assert region.props["commit_sha"] == COMMIT_B
        assert region.props["file_id"] == f"{SHORT}:file:httpx/_client.py@{COMMIT_B}"
        assert region.props["content_hash"] == "deadbeef"

    def test_review_region_links_comment_and_commit(self) -> None:
        plan = ops_from_records(CORPUS)
        region = f"{SHORT}:region:httpx/_client.py:240:243"
        comment = f"{SHORT}:review_comment:456"
        commit = f"{SHORT}:commit:{COMMIT_B}"
        rels = {
            (rel.rel_type, rel.from_record_id, rel.to_record_id)
            for rel in plan.rels
        }
        assert ("COMMENTS_ON", comment, region) in rels
        assert ("CHANGED_IN", region, commit) in rels


