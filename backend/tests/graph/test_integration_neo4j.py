"""Integration contracts against real Neo4j/AuraDB. Opt-in only:
skipped unless WHYCODE_TEST_NEO4J_URI is set, so the ordinary red run
never depends on a live database. These mirror the Component 2
acceptance list from the plan: constraints real, no orphans, rebuild
without duplicates, and the full path for one known PR."""

import os

import pytest
from graph_fixtures import DEEP, SHORT
from test_builder import corpus_jsonl

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("WHYCODE_TEST_NEO4J_URI"),
        reason="set WHYCODE_TEST_NEO4J_URI (plus _USER/_PASSWORD/_DATABASE) to opt in",
    ),
]


@pytest.fixture()
def neo4j_session():
    from neo4j import GraphDatabase

    uri = os.environ["WHYCODE_TEST_NEO4J_URI"]
    user = os.environ.get("WHYCODE_TEST_NEO4J_USER", "neo4j")
    password = os.environ["WHYCODE_TEST_NEO4J_PASSWORD"]
    database = os.environ.get("WHYCODE_TEST_NEO4J_DATABASE", "neo4j")
    driver = GraphDatabase.driver(uri, auth=(user, password))
    with driver.session(database=database) as session:
        yield session
    driver.close()


def wipe_graph(session) -> None:
    session.run("MATCH (n) DETACH DELETE n")


class TestAcceptanceOnRealNeo4j:
    @pytest.fixture(autouse=True)
    def _load_corpus(self, neo4j_session):
        from app.graph.builder import GraphBuilder

        wipe_graph(neo4j_session)
        self.builder = GraphBuilder(session=neo4j_session)
        self.builder.prepare_schema()
        self.builder.load_jsonl_text(corpus_jsonl(), repo="encode/httpx")

    def test_review_comment_anchors_a_code_region(self, neo4j_session) -> None:
        result = neo4j_session.run(
            "MATCH (:ReviewComment {record_id: $rid})-[:COMMENTS_ON]->(r:CodeRegion) "
            "RETURN count(r) AS c",
            rid=f"{SHORT}:review_comment:456",
        ).single()
        assert result is not None and result["c"] == 1

    def test_every_commit_is_reachable_from_its_pr(self, neo4j_session) -> None:
        result = neo4j_session.run(
            "MATCH (:PullRequest {record_id: $rid})-[:HAS_COMMIT]->(c:Commit) "
            "RETURN count(c) AS c",
            rid=f"{SHORT}:pr:{DEEP}",
        ).single()
        assert result is not None and result["c"] == 2

    def test_no_orphan_nodes_after_load(self, neo4j_session) -> None:
        result = neo4j_session.run(
            "MATCH (n) WHERE n.record_id IS NULL AND n.label <> 'Repo' RETURN count(n) AS c"
        ).single()
        assert result is not None and result["c"] == 0

    def test_rerun_does_not_duplicate_nodes(self, neo4j_session) -> None:
        before = neo4j_session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        self.builder.load_jsonl_text(corpus_jsonl(), repo="encode/httpx")
        after = neo4j_session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        assert before == after

    def test_rebuild_wipes_and_reloads_scoped_to_repo(self, neo4j_session) -> None:
        self.builder.rebuild_jsonl_text(corpus_jsonl(), repo="encode/httpx")
        before = neo4j_session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        self.builder.rebuild_jsonl_text(corpus_jsonl(), repo="encode/httpx")
        after = neo4j_session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        assert before == after

    def test_known_pr_has_a_full_evidence_path(self, neo4j_session) -> None:
        result = neo4j_session.run(
            "MATCH path = (pr:PullRequest {record_id: $rid})-[:HAS_REVIEW]->"
            "(:Review)-[:HAS_COMMENT]->(:ReviewComment)-[:COMMENTS_ON]-(:CodeRegion) "
            "RETURN length(path) AS hops",
            rid=f"{SHORT}:pr:{DEEP}",
        ).single()
        assert result is not None and result["hops"] == 4

    def test_constraints_actually_enforce_uniqueness(self, neo4j_session) -> None:
        from neo4j.exceptions import Neo4jError

        with pytest.raises(Neo4jError):
            neo4j_session.run(
                "CREATE (:PullRequest {record_id: $rid, repo: $repo})",
                rid=f"{SHORT}:pr:{DEEP}",
                repo="encode/httpx",
            )
