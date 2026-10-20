"""GraphPlan: the pure, database-free unit of work.

The builder either executes a plan or refuses it wholesale. Keeping nodes,
relationships, integrity checks and counts in one immutable-ish object is
what makes "fail before the first write" (D-0011) enforceable and what
makes dry-run indistinguishable from a real load except for the writes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.core.errors import InvalidRecordError
from app.domain.evidence_ids import (
    code_region_id,
    commit_id,
    doc_id,
    issue_comment_id,
    issue_id,
    person_id,
    pr_file_id,
    pull_request_id,
    repo_id,
    review_comment_id,
    review_id,
)
from app.domain.record_types import RecordType
from app.graph.records import GraphNode, GraphRecords, GraphRelationship

LABEL_BY_TYPE: dict[str, str] = {
    RecordType.PULL_REQUEST.value: "PullRequest",
    RecordType.COMMIT.value: "Commit",
    RecordType.CHANGED_FILE.value: "ChangedFile",
    RecordType.REVIEW.value: "Review",
    RecordType.REVIEW_COMMENT.value: "ReviewComment",
    RecordType.ISSUE.value: "Issue",
    RecordType.ISSUE_COMMENT.value: "IssueComment",
    RecordType.PERSON.value: "Person",
    RecordType.DOC_FILE.value: "Doc",
}


@dataclass(frozen=True)
class NodeOp:
    label: str
    record_id: str
    props: dict[str, Any]


@dataclass(frozen=True)
class RelOp:
    rel_type: str
    from_record_id: str
    to_record_id: str
    props: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphPlan:
    nodes: list[NodeOp] = field(default_factory=list)
    rels: list[RelOp] = field(default_factory=list)

    @classmethod
    def from_records(
        cls,
        records: Iterable[dict[str, Any]],
        *,
        high_signal_pr_numbers: set[int] | None = None,
    ) -> GraphPlan:
        from app.graph.mapping import ops_from_records

        return ops_from_records(
            records, high_signal_pr_numbers=high_signal_pr_numbers
        )

    def to_records(self) -> GraphRecords:
        """The plan as transport-neutral records.

        Integrity is asserted here so a writer can never receive a batch with
        a dangling endpoint, whether it came from a plan or from extraction.
        """
        self.assert_integrity()
        return GraphRecords(
            nodes=[
                GraphNode(label=node.label, key=node.record_id, properties=node.props)
                for node in self.nodes
            ],
            relationships=[
                GraphRelationship(
                    rel_type=rel.rel_type,
                    start_key=rel.from_record_id,
                    end_key=rel.to_record_id,
                    properties=rel.props,
                )
                for rel in self.rels
            ],
        )

    def node(self, record_id: str) -> NodeOp | None:
        for node in self.nodes:
            if node.record_id == record_id:
                return node
        return None

    def counts(self) -> dict[str, dict[str, int]]:
        nodes: dict[str, int] = {}
        rels: dict[str, int] = {}
        for node in self.nodes:
            nodes[node.label] = nodes.get(node.label, 0) + 1
        for rel in self.rels:
            rels[rel.rel_type] = rels.get(rel.rel_type, 0) + 1
        return {"nodes": nodes, "rels": rels}

    def assert_integrity(self) -> None:
        known = {node.record_id for node in self.nodes}
        for rel in self.rels:
            missing = [
                rid
                for rid in (rel.from_record_id, rel.to_record_id)
                if rid not in known
            ]
            if missing:
                raise InvalidRecordError(
                    f"relationship {rel.rel_type} references missing record(s): "
                    f"{', '.join(missing)}",
                    component="graph",
                    record_id=missing[0],
                )


# Helpers kept here (not in mapping) so the plan vocabulary is one module.
def _node(label: str, record_id: str, props: dict[str, Any], repo: str) -> NodeOp:
    return NodeOp(
        label=label,
        record_id=record_id,
        props={**props, "record_id": record_id, "repo": repo},
    )


__all__ = [
    "GraphPlan",
    "NodeOp",
    "RelOp",
    "LABEL_BY_TYPE",
    "code_region_id",
    "commit_id",
    "doc_id",
    "issue_comment_id",
    "issue_id",
    "person_id",
    "pr_file_id",
    "pull_request_id",
    "repo_id",
    "review_comment_id",
    "review_id",
    "_node",
]
