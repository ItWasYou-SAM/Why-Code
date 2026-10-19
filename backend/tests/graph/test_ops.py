"""GraphPlan op-stream contracts (pure, no database anywhere here).

The plan is the unit of work the builder can either execute or refuse:
nodes, rels, integrity, counts. Everything Neo4j-specific lives behind
the adapter, so if AuraDB semantics change, the mutation reads here."""

import pytest
from graph_fixtures import (
    CORPUS,
    DEEP,
    DEEP_ISSUE,
    REGION_ID,
    SHORT,
    dangling_corpus,
    expected_counts,
    record_by_id,
)

from app.core.errors import InvalidRecordError


def make_plan(records=None):
    from app.graph.ops import GraphPlan

    return GraphPlan.from_records(CORPUS if records is None else records)


class TestNodeOpsFromCorpus:
    def test_every_record_type_becomes_its_label(self) -> None:
        plan = make_plan()
        labels = {n.label for n in plan.nodes}
        assert labels == {
            "Repo",
            "PullRequest",
            "Commit",
            "ChangedFile",
            "Review",
            "ReviewComment",
            "Issue",
            "IssueComment",
            "Person",
            "Doc",
            "CodeRegion",
        }

    def test_nodes_keyed_by_their_source_record_id(self) -> None:
        plan = make_plan()
        node = plan.node(f"{SHORT}:pr:{DEEP}")
        assert node is not None
        assert node.record_id == f"{SHORT}:pr:{DEEP}"
        assert node.props["title"] == "Fix retry handling of pool timeouts"

    def test_repo_node_exists_once(self) -> None:
        plan = make_plan()
        repos = [n for n in plan.nodes if n.label == "Repo"]
        assert len(repos) == 1
        assert repos[0].record_id.startswith(f"{SHORT}:repo")

    def test_plan_counts_match_expected(self) -> None:
        plan = make_plan()
        assert plan.counts() == expected_counts()

    def test_duplicate_record_ids_are_merged_once(self) -> None:
        """Replayed corpus lines are a property of reruns; the plan must
        dedupe rather than double-write the same node."""
        plan = make_plan()
        pr_ops = [n for n in plan.nodes if n.record_id == f"{SHORT}:pr:{DEEP}"]
        assert len(pr_ops) == 1


class TestCodeRegionRules:
    def test_region_exists_for_anchored_review_comment(self) -> None:
        plan = make_plan()
        node = plan.node(REGION_ID)
        assert node is not None
        assert node.label == "CodeRegion"
        assert node.props["start_line"] == 240
        assert node.props["line"] == 243

    def test_region_linked_from_file_and_comment(self) -> None:
        plan = make_plan()
        rels = {r.rel_type: (r.from_record_id, r.to_record_id) for r in plan.rels}
        file_id = f"{SHORT}:pr:{DEEP}:file:httpx/_client.py"
        comment_id = f"{SHORT}:review_comment:456"
        assert rels["HAS_REGION"] == (file_id, REGION_ID)
        assert rels["COMMENTS_ON"] == (comment_id, REGION_ID)

    def test_review_comment_without_anchor_makes_no_region(self) -> None:
        base_comment = record_by_id(f"{SHORT}:review_comment:456")
        anchorless = {
            **base_comment,
            "payload": {**base_comment["payload"], "line": None, "start_line": None},
        }
        records = [
            record_by_id(f"{SHORT}:review:101"),
            anchorless,
            record_by_id(f"{SHORT}:pr:{DEEP}:file:httpx/_client.py"),
        ]
        plan = make_plan(records)
        regions = [n for n in plan.nodes if n.label == "CodeRegion"]
        assert regions == []


class TestRelOpsFromCorpus:
    def test_rel_endpoints_reference_existing_nodes(self) -> None:
        plan = make_plan()
        node_ids = {n.record_id for n in plan.nodes}
        for rel in plan.rels:
            assert rel.from_record_id in node_ids, f"dangling from {rel}"
            assert rel.to_record_id in node_ids, f"dangling to {rel}"

    def test_authorship_edges(self) -> None:
        plan = make_plan()
        authored = sorted(
            (r for r in plan.rels if r.rel_type == "AUTHORED"),
            key=lambda r: (r.from_record_id, r.to_record_id),
        )
        endpoints = {(r.from_record_id, r.to_record_id) for r in authored}
        assert (f"{SHORT}:person:tomchristie", f"{SHORT}:pr:{DEEP}") in endpoints
        assert (f"{SHORT}:person:tomchristie", f"{SHORT}:commit:{'a' * 40}") in endpoints

    def test_discussion_chain_pr_issue_comment(self) -> None:
        plan = make_plan()
        rels = {r.rel_type: (r.from_record_id, r.to_record_id) for r in plan.rels}
        assert rels["DISCUSSES"] == (f"{SHORT}:pr:{DEEP}", f"{SHORT}:issue:{DEEP_ISSUE}")
        issue_comment_edges = [
            (r.from_record_id, r.to_record_id)
            for r in plan.rels
            if r.rel_type == "HAS_COMMENT" and r.to_record_id == f"{SHORT}:issue_comment:789"
        ]
        assert issue_comment_edges == [
            (f"{SHORT}:issue:{DEEP_ISSUE}", f"{SHORT}:issue_comment:789")
        ]

    def test_review_chain_pr_review_comment(self) -> None:
        plan = make_plan()
        rels = {r.rel_type: (r.from_record_id, r.to_record_id) for r in plan.rels}
        assert rels["HAS_REVIEW"] == (f"{SHORT}:pr:{DEEP}", f"{SHORT}:review:101")
        review_comment_edges = [
            (r.from_record_id, r.to_record_id)
            for r in plan.rels
            if r.rel_type == "HAS_COMMENT"
            and r.to_record_id == f"{SHORT}:review_comment:456"
        ]
        assert review_comment_edges == [(f"{SHORT}:review:101", f"{SHORT}:review_comment:456")]


class TestIntegrityFailFast:
    def test_dangling_endpoint_refuses_the_whole_corpus(self) -> None:
        with pytest.raises(InvalidRecordError) as error:
            make_plan(dangling_corpus())
        assert "review_comment:999" in str(error.value)

    def test_unknown_record_type_is_rejected(self) -> None:
        records = [dict(record_by_id(f"{SHORT}:pr:{DEEP}"))]
        records[0] = {**records[0], "record_type": "gist"}
        with pytest.raises(InvalidRecordError):
            make_plan(records)

    def test_record_without_id_is_rejected(self) -> None:
        broken = dict(record_by_id(f"{SHORT}:pr:{DEEP}"))
        del broken["record_id"]
        with pytest.raises(InvalidRecordError):
            make_plan([broken])


class TestPlanReport:
    def test_report_shape_is_label_and_type_counts(self) -> None:
        report = make_plan().counts()
        assert set(report) == {"nodes", "rels"}
        assert report["rels"]["AUTHORED"] == 5

    def test_plan_is_ordered_for_deterministic_replay(self) -> None:
        """Byte-identical plan from the same corpus in one pass order:
        non-deterministic order would make diff-based debugging of
        rebuilds impossible."""
        left = make_plan()
        right = make_plan()
        assert [n.record_id for n in left.nodes] == [n.record_id for n in right.nodes]
        assert [(r.rel_type, r.from_record_id, r.to_record_id) for r in left.rels] == [
            (r.rel_type, r.from_record_id, r.to_record_id) for r in right.rels
        ]
