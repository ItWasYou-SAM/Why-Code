"""Builder orchestration contracts: schema, load, dry-run, rebuild.

Runs against the recorded FakeGraphSession — this is where the rules
become operations, and where the silence rules live (dry-run writes
nothing, integrity failure means failure before the first write)."""

import json

import pytest
from graph_fakes import FakeGraphSession
from graph_fixtures import CORPUS, DEEP, SHORT


def corpus_jsonl() -> str:
    return "\n".join(json.dumps(r) for r in CORPUS)


def run_builder():
    from app.graph.builder import GraphBuilder

    session = FakeGraphSession()
    builder = GraphBuilder(session=session)
    report = builder.load_jsonl_text(corpus_jsonl(), repo="encode/httpx")
    return session, builder, report


class TestSchema:
    def test_prepare_schema_writes_constraints_before_any_merge(self) -> None:
        from app.graph.builder import GraphBuilder

        session = FakeGraphSession()
        GraphBuilder(session=session).prepare_schema()
        assert session.executed > 0
        first = session.calls[0][0]
        assert "IS UNIQUE" in first

    def test_prepare_schema_is_idempotent_scan(self) -> None:
        from app.graph.builder import GraphBuilder

        session = FakeGraphSession()
        builder = GraphBuilder(session=session)
        builder.prepare_schema()
        twice = session.executed
        builder.prepare_schema()
        assert session.executed == 2 * twice


class TestLoad:
    def test_load_writes_the_whole_corpus(self) -> None:
        session, _, report = run_builder()
        assert session.executed > 0
        assert report.nodes["PullRequest"] == 2
        assert report.rels["HAS_COMMIT"] == 3

    def test_load_is_traceable_through_source_record_ids(self) -> None:
        session, _, _ = run_builder()
        node_keys = {
            entry["key"]
            for params in session.parameters_seen
            for entry in params.get("nodes", [])
        }
        assert f"{SHORT}:pr:{DEEP}" in node_keys

    def test_load_never_interpolates_record_ids_into_cypher(self) -> None:
        """Same string-interpolation ban as the adapter level; asserted
        here through the real orchestration path."""
        session, _, _ = run_builder()
        node_keys = [
            str(entry.get("key"))
            for params in session.parameters_seen
            for entry in params.get("nodes", [])
        ]
        for query, _ in session.calls:
            for node_key in node_keys:
                assert node_key not in query, f"leaky cypher: {query}"

    def test_same_corpus_twice_executes_the_same_number_of_merges(self) -> None:
        from app.graph.builder import GraphBuilder

        session = FakeGraphSession()
        builder = GraphBuilder(session=session)
        first = builder.load_jsonl_text(corpus_jsonl(), repo="encode/httpx")
        count = session.executed
        second = builder.load_jsonl_text(corpus_jsonl(), repo="encode/httpx")
        assert session.executed == 2 * count
        assert first.counts_shape() == second.counts_shape()

    def test_load_reports_and_is_reentrant(self) -> None:
        from app.graph.builder import GraphBuilder

        session = FakeGraphSession()
        builder = GraphBuilder(session=session)
        report = builder.load_jsonl_text(corpus_jsonl(), repo="encode/httpx")
        assert report.nodes["Person"] == 4
        assert report.rels["COMMENTED"] == 2


class TestFailFastBeforeWrite:
    def test_integrity_error_causes_zero_database_operations(self) -> None:
        from app.core.errors import InvalidRecordError
        from app.graph.builder import GraphBuilder

        session = FakeGraphSession()
        builder = GraphBuilder(session=session)
        with_open_ops = [{**r} for r in CORPUS]
        with_open_ops.append(
            {
                "record_id": f"{SHORT}:review_comment:999",
                "record_type": "review_comment",
                "repo": "encode/httpx",
                "pr_number": DEEP,
                "payload": {
                    "path": "httpx/_missing.py",
                    "line": 5,
                    "start_line": None,
                    "side": "RIGHT",
                    "body": "anchors a file that is not in the corpus",
                    "author": "jeez",
                },
                "content_hash": "x",
                "ingested_at": "2026-10-19T10:00:00Z",
            }
        )
        with pytest.raises(InvalidRecordError):
            builder.load_records(with_open_ops, repo="encode/httpx")
        assert session.executed == 0

    def test_fail_fast_applies_to_clean_plans_then_writes_happen(self) -> None:
        session, _, _ = run_builder()
        assert session.executed > 0


class TestDryRun:
    def test_dry_run_performs_zero_operations(self) -> None:
        from app.graph.builder import GraphBuilder

        session = FakeGraphSession()
        builder = GraphBuilder(session=session)
        report = builder.load_jsonl_text(corpus_jsonl(), repo="encode/httpx", dry_run=True)
        assert session.executed == 0
        assert report.nodes["PullRequest"] == 2
        assert report.rels["DISCUSSES"] == 1


class TestRebuild:
    def test_rebuild_scopes_delete_to_the_repo(self) -> None:
        from app.graph.builder import GraphBuilder

        session = FakeGraphSession()
        builder = GraphBuilder(session=session)
        builder.rebuild_jsonl_text(corpus_jsonl(), repo="encode/httpx")
        queries = [q for q, _ in session.calls]
        assert any("DETACH DELETE" in q for q in queries)
        delete_params = [p for q, p in session.calls if "DETACH DELETE" in q]
        assert all(p.get("repo") == "encode/httpx" for p in delete_params)

    def test_rebuild_is_idempotent_in_counts(self) -> None:
        from app.graph.builder import GraphBuilder

        session = FakeGraphSession()
        builder = GraphBuilder(session=session)
        first = builder.rebuild_jsonl_text(corpus_jsonl(), repo="encode/httpx")
        session.calls.clear()
        second = builder.rebuild_jsonl_text(corpus_jsonl(), repo="encode/httpx")
        assert first.nodes == second.nodes
        assert first.rels == second.rels
        assert session.calls
