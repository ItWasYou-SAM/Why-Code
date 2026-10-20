"""Graph-record contracts: build records first, write them in batches.

No Cypher is built by extraction or mapping; these tests pin the record
shape, its integrity rules, and the batched translation the writers use.
"""

import pytest
from graph_fakes import FakeGraphSession
from graph_fixtures import CORPUS
from pydantic import ValidationError

from app.core.errors import InvalidRecordError
from app.graph.batch_writer import (
    merge_node_batch_statement,
    record_statements,
    write_records,
)
from app.graph.records import GraphNode, GraphRecords, GraphRelationship


class TestModels:
    def test_node_requires_label_and_key(self) -> None:
        assert GraphNode(label="Symbol", key="k").properties == {}
        with pytest.raises(ValidationError):
            GraphNode(label="", key="k")
        with pytest.raises(ValidationError):
            GraphNode(label="Symbol", key="")

    def test_label_must_be_a_safe_identifier(self) -> None:
        with pytest.raises(ValidationError):
            GraphNode(label="Symbol`) DETACH DELETE n //", key="k")

    def test_relationship_rejects_unsafe_identifier(self) -> None:
        with pytest.raises(ValidationError):
            GraphRelationship(rel_type="DEFINES]->() //", start_key="a", end_key="b")

    def test_default_properties_are_not_shared(self) -> None:
        first = GraphNode(label="File", key="a")
        second = GraphNode(label="File", key="b")
        first.properties["path"] = "x"
        assert second.properties == {}

    def test_compatibility_aliases_match_the_shared_shape(self) -> None:
        node = GraphNode(label="File", key="a", properties={"path": "x"})
        assert node.node_id == node.key == "a"
        assert node.props == node.properties
        rel = GraphRelationship(rel_type="DEFINES", start_key="a", end_key="b")
        assert (rel.from_id, rel.to_id) == (rel.start_key, rel.end_key)


class TestIntegrity:
    def test_counts_group_by_label_and_type(self) -> None:
        records = GraphRecords(
            nodes=[
                GraphNode(label="File", key="a"),
                GraphNode(label="File", key="b"),
                GraphNode(label="Symbol", key="c"),
            ],
            relationships=[
                GraphRelationship(rel_type="DEFINES", start_key="a", end_key="c"),
                GraphRelationship(rel_type="DEFINES", start_key="b", end_key="c"),
            ],
        )
        assert records.counts() == {
            "nodes": {"File": 2, "Symbol": 1},
            "rels": {"DEFINES": 2},
        }

    def test_duplicate_node_key_is_refused(self) -> None:
        records = GraphRecords(
            nodes=[
                GraphNode(label="File", key="a"),
                GraphNode(label="File", key="a"),
            ]
        )
        with pytest.raises(InvalidRecordError):
            records.assert_integrity()

    def test_dangling_endpoint_is_refused(self) -> None:
        records = GraphRecords(
            nodes=[GraphNode(label="File", key="a")],
            relationships=[
                GraphRelationship(rel_type="DEFINES", start_key="a", end_key="gone")
            ],
        )
        with pytest.raises(InvalidRecordError):
            records.assert_integrity()


class TestJsonlRoundTrip:
    def test_records_survive_a_jsonl_round_trip(self) -> None:
        from app.graph.records import records_from_jsonl, records_to_jsonl

        records = GraphRecords(
            nodes=[
                GraphNode(label="File", key="r:file:a.py@c", properties={"path": "a.py"}),
                GraphNode(label="Symbol", key="r:symbol:a.py@c:f", properties={"name": "f"}),
            ],
            relationships=[
                GraphRelationship(
                    rel_type="DEFINES", start_key="r:file:a.py@c", end_key="r:symbol:a.py@c:f"
                )
            ],
        )
        restored = records_from_jsonl(records_to_jsonl(records).splitlines())
        assert restored == records

    def test_unknown_line_is_refused(self) -> None:
        from app.graph.records import records_from_jsonl

        with pytest.raises(InvalidRecordError):
            records_from_jsonl(['{"something": 1}'])


class TestFromPlan:
    def test_plan_records_mirror_ops(self) -> None:
        from app.graph.ops import GraphPlan

        plan = GraphPlan.from_records(CORPUS)
        records = plan.to_records()
        assert len(records.nodes) == len(plan.nodes)
        assert len(records.relationships) == len(plan.rels)
        assert {node.key for node in records.nodes} == {
            op.record_id for op in plan.nodes
        }
        assert all(isinstance(node, GraphNode) for node in records.nodes)
        assert all(
            isinstance(rel, GraphRelationship) for rel in records.relationships
        )

    def test_plan_records_keep_properties(self) -> None:
        from app.graph.ops import GraphPlan

        records = GraphPlan.from_records(CORPUS).to_records()
        node = next(node for node in records.nodes if node.properties.get("title"))
        assert node.properties["title"] == "Fix retry handling of pool timeouts"


class TestBatching:
    def make_records(self) -> GraphRecords:
        return GraphRecords(
            nodes=[
                GraphNode(label="File", key="a", properties={"path": "a.py"}),
                GraphNode(label="File", key="b", properties={"path": "b.py"}),
                GraphNode(label="Symbol", key="c", properties={"name": "f"}),
            ],
            relationships=[
                GraphRelationship(rel_type="DEFINES", start_key="a", end_key="c"),
                GraphRelationship(rel_type="DEFINES", start_key="b", end_key="c"),
            ],
        )

    def test_groups_by_label_and_relationship_type(self) -> None:
        statements = record_statements(self.make_records())
        queries = [query for query, _ in statements]
        assert queries[:2] == [
            merge_node_batch_statement("File"),
            merge_node_batch_statement("Symbol"),
        ]
        assert "$relationships" in queries[2]
        assert len(statements) == 3

    def test_batch_size_chunks_a_group(self) -> None:
        statements = record_statements(self.make_records(), batch_size=1)
        assert len(statements) == 5
        node_batches = [params["nodes"] for _q, params in statements if "nodes" in params]
        assert all(len(batch) == 1 for batch in node_batches)

    def test_nodes_are_written_before_relationships(self) -> None:
        statements = record_statements(self.make_records())
        kinds = [
            "nodes" if "nodes" in params else "relationships"
            for _query, params in statements
        ]
        assert kinds.index("relationships") == 2
        assert kinds == ["nodes", "nodes", "relationships"]

    def test_integrity_is_checked_before_translation(self) -> None:
        records = GraphRecords(
            nodes=[GraphNode(label="File", key="a")],
            relationships=[
                GraphRelationship(rel_type="DEFINES", start_key="a", end_key="gone")
            ],
        )
        with pytest.raises(InvalidRecordError):
            record_statements(records)

    def test_batch_size_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            record_statements(self.make_records(), batch_size=0)

    def test_write_records_executes_one_statement_per_group(self) -> None:
        session = FakeGraphSession()
        executed = write_records(session, self.make_records())
        assert executed == 3
        assert session.executed == 3

    def test_empty_records_write_nothing(self) -> None:
        session = FakeGraphSession()
        write_records(session, GraphRecords(), batch_size=10)
        assert session.executed == 0
