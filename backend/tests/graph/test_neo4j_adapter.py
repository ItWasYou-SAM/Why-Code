"""Neo4j adapter contracts: a plan becomes batched, parameterized Cypher.

Statement shapes are pinned because identical Cypher across runs is what
makes replays debuggable. Identical node labels / relationship types share
one ``UNWIND`` statement and every value travels as a parameter; if a Cypher
shape must change, change test + decision together.
"""

from graph_fixtures import SHORT  # noqa: F401  (SHORT referenced inside test bodies)
from test_ops import make_plan

from app.graph.neo4j_adapter import (
    constraint_name,
    constraint_statements,
    delete_nodes,
    index_name,
    index_statements,
    translate,
)


class TestStatementShapes:
    def test_node_batch_is_parameterized(self) -> None:
        from app.graph.batch_writer import merge_node_batch_statement

        statement = merge_node_batch_statement(label="PullRequest")
        assert statement == (
            "UNWIND $nodes AS node "
            "MERGE (n:PullRequest {record_id: node.key}) "
            "SET n += node.properties"
        )

    def test_relationship_batch_is_parameterized(self) -> None:
        from app.graph.batch_writer import merge_relationship_batch_statement

        statement = merge_relationship_batch_statement(rel_type="HAS_COMMIT")
        assert statement == (
            "UNWIND $relationships AS rel "
            "MATCH (a {record_id: rel.start_key}) "
            "MATCH (b {record_id: rel.end_key}) "
            "MERGE (a)-[r:HAS_COMMIT]->(b) "
            "SET r += rel.properties"
        )

    def test_constraint_names_are_label_derived(self) -> None:
        assert constraint_name("PullRequest") == "whycode_pullrequest_record_id_unique"
        assert constraint_name("CodeRegion") == "whycode_coderegion_record_id_unique"

    def test_index_names_are_label_derived(self) -> None:
        assert index_name("PullRequest") == "whycode_pullrequest_repo_idx"

    def test_delete_is_scoped_to_repo_label(self) -> None:
        statement = delete_nodes(label="Doc")
        assert "MATCH (n:Doc {repo: $repo})" in statement
        assert "DETACH DELETE n" in statement


class TestTranslation:
    def test_each_label_becomes_one_statement(self) -> None:
        plan = make_plan()
        statements = translate(plan)
        labels = {node.label for node in plan.nodes}
        assert len(statements) == len(labels) + len({rel.rel_type for rel in plan.rels})

    def test_batch_size_splits_a_label(self) -> None:
        from app.graph.batch_writer import record_statements

        plan = make_plan()
        batched = record_statements(plan.to_records(), batch_size=1)
        # Every statement carries at most one entry, so one per node or rel.
        assert len(batched) == len(plan.nodes) + len(plan.rels)

    def test_translation_params_carry_source_record_ids(self) -> None:
        plan = make_plan()
        node_keys = {
            entry["key"]
            for _query, params in translate(plan)
            if "nodes" in params
            for entry in params["nodes"]
        }
        assert f"{SHORT}:commit:{'a' * 40}" in node_keys

    def test_merge_never_uses_string_interpolation_for_payload_values(self) -> None:
        """Every value flows as $params. Bracing values into Cypher is
        injection risk plus unfingerprintable debug output."""
        plan = make_plan()
        for query, params in translate(plan):
            entries = [*params.get("nodes", []), *params.get("relationships", [])]
            for entry in entries:
                for value in entry.values():
                    if not isinstance(value, str):
                        continue
                    assert value not in query, f"leaky cypher: {query}"


class TestConstraintSweep:
    def test_constraints_exist_for_every_plan_label(self) -> None:
        plan = make_plan()
        labels = {n.label for n in plan.nodes}
        assert {"PullRequest", "Commit", "CodeRegion", "Person", "Doc"} <= labels

        constraints = list(constraint_statements(labels))
        for statement in constraints:
            assert "IF NOT EXISTS" in statement
        assert len(constraints) == len(labels)

    def test_schema_statements_are_constraints_then_indexes(self) -> None:
        from app.graph.neo4j_adapter import schema_statements

        statements = list(schema_statements(["File", "Symbol"]))
        assert statements[:2] == list(
            constraint_statements(["File", "Symbol"])
        )
        assert statements[2:] == list(index_statements(["File", "Symbol"]))
        assert len(statements) == 4
