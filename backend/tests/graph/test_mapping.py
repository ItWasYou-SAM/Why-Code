"""Mapping-rule contracts: record → plan is rule-driven and total.

This module pins the RULES (what each record type means to the graph),
not their implementation. If one of these tests breaks, the semantic of
the graph change itself must be discussed, not refactored away."""

import pytest
from fixtures import CORPUS, DEEP, DEEP_ISSUE, REGION_ID, SHORT, record_by_id

from app.core.errors import InvalidRecordError


def plan_for_corpus():
    from app.graph.mapping import ops_from_records

    return ops_from_records(CORPUS)


class TestLabelMapping:
    def test_pull_request_maps_to_pullrequest(self) -> None:
        plan = plan_for_corpus()
        node = plan.node(f"{SHORT}:pr:{DEEP}")
        assert node.label == "PullRequest"

    def test_issue_comment_maps_to_issuecomment(self) -> None:
        plan = plan_for_corpus()
        node = plan.node(f"{SHORT}:issue_comment:789")
        assert node.label == "IssueComment"

    def test_person_maps_to_person(self) -> None:
        plan = plan_for_corpus()
        node = plan.node(f"{SHORT}:person:tomchristie")
        assert node.label == "Person"

    def test_doc_file_maps_to_doc(self) -> None:
        plan = plan_for_corpus()
        node = plan.node(f"{SHORT}:doc:docs/api.md")
        assert node.label == "Doc"


class TestTraceability:
    def test_nodes_carry_source_record_id(self) -> None:
        plan = plan_for_corpus()
        for node in plan.nodes:
            assert node.props.get("record_id") == node.record_id

    def test_nodes_carry_repo_scope(self) -> None:
        plan = plan_for_corpus()
        for node in plan.nodes:
            assert node.props.get("repo") == "encode/httpx"


class TestPersonEdges:
    def test_reviewer_becomes_reviewed_edge(self) -> None:
        plan = plan_for_corpus()
        pairs = [
            (r.from_record_id, r.to_record_id)
            for r in plan.rels
            if r.rel_type == "REVIEWED"
        ]
        assert (f"{SHORT}:person:arbonron", f"{SHORT}:pr:{DEEP}") in pairs

    def test_commenters_become_commented_edges(self) -> None:
        plan = plan_for_corpus()
        pairs = [
            (r.from_record_id, r.to_record_id)
            for r in plan.rels
            if r.rel_type == "COMMENTED"
        ]
        assert (f"{SHORT}:person:jacobk", f"{SHORT}:issue_comment:789") in pairs
        assert (f"{SHORT}:person:arbonron", f"{SHORT}:review_comment:456") in pairs


class TestCodeRegionIdConvention:
    def test_region_id_is_deterministic_slashed(self) -> None:
        """D-0010: {short}:region:{path}:{start}:{line}. Changing this
        would silently orphan every COMMENTS_ON edge already shipped."""
        assert f"{SHORT}:region:httpx/_client.py:240:243" == REGION_ID
        plan = plan_for_corpus()
        region = plan.node(REGION_ID)
        assert region is not None


class TestIssueCommentTargetRules:
    def test_issue_comment_with_issue_number_links_issue(self) -> None:
        plan = plan_for_corpus()
        pairs = [
            (r.from_record_id, r.to_record_id)
            for r in plan.rels
            if r.rel_type == "HAS_COMMENT"
            and r.to_record_id == f"{SHORT}:issue_comment:789"
        ]
        assert pairs == [(f"{SHORT}:issue:{DEEP_ISSUE}", f"{SHORT}:issue_comment:789")]

    def test_issue_comment_without_issue_number_falls_back_to_pr(self) -> None:
        from app.graph.mapping import ops_from_records

        comment = dict(record_by_id(f"{SHORT}:issue_comment:789"))
        comment = {
            **comment,
            "payload": {
                key: value for key, value in comment["payload"].items() if key != "issue_number"
            },
        }
        plan = ops_from_records(
            [comment] + [r for r in CORPUS if r["record_id"] != "httpx:issue_comment:789"]
        )
        pairs = [
            (r.from_record_id, r.to_record_id)
            for r in plan.rels
            if r.rel_type == "HAS_COMMENT" and r.to_record_id == f"{SHORT}:issue_comment:789"
        ]
        assert pairs == [(f"{SHORT}:pr:{DEEP}", f"{SHORT}:issue_comment:789")]

    def test_issue_comment_with_no_target_context_is_rejected(self) -> None:
        from app.graph.mapping import ops_from_records

        comment = dict(record_by_id(f"{SHORT}:issue_comment:789"))
        orphan = {
            **comment,
            "payload": {
                key: value for key, value in comment["payload"].items() if key != "issue_number"
            },
            "pr_number": None,
        }
        with pytest.raises(InvalidRecordError):
            ops_from_records([orphan])

    def test_issue_comment_on_unknown_issue_is_integrity_error(self) -> None:
        from app.graph.mapping import ops_from_records

        original = record_by_id(f"{SHORT}:issue_comment:789")
        comment = {**original, "payload": {**original["payload"], "issue_number": 999_999}}
        with pytest.raises(InvalidRecordError):
            ops_from_records([comment])
