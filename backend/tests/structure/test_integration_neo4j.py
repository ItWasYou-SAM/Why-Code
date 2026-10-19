"""Real-AuraDB acceptance for the structural layer. Opt-in via
WHYCODE_TEST_NEO4J_URI, exactly like the graph suite. Mirrors the
plan's Component 3 acceptance: symbol exists for the known PR file,
valid spans, symbol↔region link, imports resolve, unparseable file
creates nothing."""

import os

import pytest
from fakes import content_provider, corpus_records

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("WHYCODE_TEST_NEO4J_URI"),
        reason="set WHYCODE_TEST_NEO4J_URI (plus _USER/_PASSWORD/_DATABASE) to opt in",
    ),
]

CLIENT_FILE_ID = "httpx:pr:1131:file:httpx/_client.py"
CLIENT_SYMBOL_ID = "httpx:symbol:httpx/_client.py:Client"


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


def one(session, query: str, **parameters: object):
    return session.run(query, **parameters).single()


@pytest.fixture()
def enriched(neo4j_session):
    from fakes import LoggerSpy

    from app.graph.builder import GraphBuilder
    from app.structure.service import StructureEnricher

    wipe_graph(neo4j_session)
    builder = GraphBuilder(session=neo4j_session)
    builder.prepare_schema()
    plan = StructureEnricher(content_provider(), LoggerSpy()).enrich(corpus_records())
    builder.load_plan(plan, repo="encode/httpx")
    return neo4j_session


class TestStructuralAcceptance:
    def test_symbol_exists_for_known_pr_file(self, enriched) -> None:
        result = one(
            enriched,
            "MATCH (s:Symbol {record_id: $rid}) RETURN count(s) AS c",
            rid=CLIENT_SYMBOL_ID,
        )
        assert result["c"] == 1

    def test_symbol_has_valid_line_bounds(self, enriched) -> None:
        result = one(
            enriched,
            "MATCH (s:Symbol {record_id: $rid})-[:HAS_REGION]->(r:CodeRegion) "
            "RETURN r.start_line AS start, r.end_line AS end",
            rid=CLIENT_SYMBOL_ID,
        )
        assert result["start"] >= 1
        assert result["end"] >= result["start"]

    def test_symbol_links_to_region(self, enriched) -> None:
        result = one(
            enriched,
            "MATCH (:Symbol {record_id: $rid})-[:HAS_REGION]->(:CodeRegion) "
            "RETURN count(*) AS c",
            rid=CLIENT_SYMBOL_ID,
        )
        assert result["c"] == 1

    def test_changed_file_defines_symbol(self, enriched) -> None:
        result = one(
            enriched,
            "MATCH (:ChangedFile {record_id: $fid})-[:DEFINES]->(:Symbol {record_id: $sid}) "
            "RETURN count(*) AS c",
            fid=CLIENT_FILE_ID,
            sid=CLIENT_SYMBOL_ID,
        )
        assert result["c"] == 1

    def test_imports_are_file_to_file_only_when_resolvable(self, enriched) -> None:
        result = one(
            enriched,
            "MATCH (a:ChangedFile)-[:IMPORTS]->(b) WHERE b:ChangedFile OR b:ExternalRef "
            "RETURN count(*) AS c",
        )
        assert result["c"] >= 3

    def test_unparseable_file_created_nothing(self, enriched) -> None:
        result = one(
            enriched,
            "MATCH (s:Symbol) WHERE s.record_id CONTAINS 'broken/syntax.py' "
            "OR s.record_id CONTAINS 'assets/' RETURN count(s) AS c",
        )
        assert result["c"] == 0

    def test_config_key_links_to_symbol_that_mentions_it(self, enriched) -> None:
        result = one(
            enriched,
            "MATCH (k:ConfigKey {record_id: $kid})-[:USED_BY]->(:Symbol) RETURN count(*) AS c",
            kid="httpx:config:config/limits.toml:httpx.max_keepalive_connections",
        )
        assert result["c"] == 1

    def test_used_by_never_links_maxconn_impostor(self, enriched) -> None:
        result = one(
            enriched,
            "MATCH (k:ConfigKey {record_id: $kid})-[r:USED_BY]->(:Symbol) RETURN count(r) AS c",
            kid="httpx:config:config/limits.toml:httpx.max_connections",
        )
        assert result["c"] == 0
