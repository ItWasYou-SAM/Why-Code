"""Neo4j adapter contracts: each op translates to exactly one
parameterized Cypher statement. Strings are pinned because the whole
demo's replay/debug ability rests on identical statements across runs.
If a Cypher shape must change, change test + decision together."""

from fixtures import SHORT  # noqa: F401  (SHORT referenced inside test bodies)
from test_ops import make_plan

from app.graph.neo4j_adapter import (
    constraint_name,
    delete_nodes,
    index_name,
    merge_node_statement,
    merge_rel_statement,
    translate,
)


class TestStatementShapes:
    def test_node_merge_is_parameterized(self) -> None:
        statement = merge_node_statement(label="PullRequest")
        assert statement == "MERGE (n:PullRequest {record_id: $record_id}) SET n += $props"

    def test_rel_merge_is_parameterized(self) -> None:
        statement = merge_rel_statement(rel_type="HAS_COMMIT")
        assert (
            statement
            == "MERGE (a {record_id: $from_record_id}) MERGE (b {record_id: $to_record_id}) "
            "MERGE (a)-[r:HAS_COMMIT]->(b) SET r += $props"
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
    def test_translated_plan_yields_one_statement_per_op(self) -> None:
        plan = make_plan()
        statements = list(translate(plan))
        assert len(statements) == len(plan.nodes) + len(plan.rels)

    def test_translation_params_carry_source_record_ids(self) -> None:
        plan = make_plan()
        statements = list(translate(plan))
        rel_statements = [(q, p) for q, p in statements if "MERGE (a" in q]
        targets = {p["to_record_id"] for _, p in rel_statements}
        assert f"{SHORT}:commit:{'a' * 40}" in targets

    def test_merge_never_uses_string_interpolation_for_payload_values(self) -> None:
        """Every value flows as $params. Bracing values into Cypher is
        injection risk plus unfingerprintable debug output."""
        plan = make_plan()
        for query, params in translate(plan):
            literal = params.get("record_id")
            if literal is not None and (
                f'{{record_id: "{literal}"}}' in query or f"{{record_id: '{literal}'}}" in query
            ):
                raise AssertionError(f"leaky cypher: {query}")


class TestConstraintSweep:
    def test_constraints_exist_for_every_plan_label(self) -> None:
        plan = make_plan()
        labels = {n.label for n in plan.nodes}
        assert {"PullRequest", "Commit", "CodeRegion", "Person", "Doc"} <= labels
        from app.graph.neo4j_adapter import constraint_statements

        constraints = list(constraint_statements(labels))
        for statement in constraints:
            assert "IF NOT EXISTS" in statement
        assert len(constraints) == len(labels)
