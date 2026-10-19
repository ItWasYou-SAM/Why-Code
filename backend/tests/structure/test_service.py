"""Enrichment service orchestration contracts.

The service consumes changed-file/doc records, injects a content
provider (D-0015), produces a GraphPlan, and enforces the loud-skip
rules (D-0016). It must never fetch anything itself and must tolerate
poisoned files without either inventing nodes or crashing."""

from fakes import (
    FakeGraphSession,
    LoggerSpy,
    changed_file_record,
    content_provider,
    corpus_records,
    doc_file_record,
)
from sources import BROKEN_PY, CLIENT_PY, SOURCES, SYMBOL_ID


def run_enricher(records=None):
    from app.structure.service import StructureEnricher

    logger = LoggerSpy()
    records = corpus_records() if records is None else records
    plan = StructureEnricher(content_provider(), logger).enrich(records)
    return plan, logger


class TestEnrichHappyPath:
    def test_plan_received_symbols_from_both_python_files(self) -> None:
        plan, _ = run_enricher()
        symbols = {n.record_id for n in plan.nodes if n.label == "Symbol"}
        assert SYMBOL_ID["client"] in symbols
        assert SYMBOL_ID["timeout"] in symbols

    def test_binary_and_missing_sources_produce_zero_node_noise(self) -> None:
        """An extension-unknown file must not stretch the plan and must
        warn (skipped_binary), while the rest of the corpus proceeds."""
        plan, logger = run_enricher()
        labels = {n.record_id for n in plan.nodes}
        assert not any("assets/logo.png" in rid for rid in labels)
        warned = [e for e in logger.warnings if e[1].get("event") == "skipped_binary"]
        assert any("logo.png" in e[1].get("path", "") for e in warned)

    def test_broken_python_yields_warning_and_no_nodes(self) -> None:
        plan, logger = run_enricher()
        ids = {n.record_id for n in plan.nodes}
        assert not any("broken/syntax.py" in rid for rid in ids)
        warned = [e for e in logger.warnings if e[1].get("event") == "unparseable_file"]
        assert any("broken/syntax.py" in e[1].get("path", "") for e in warned)

    def test_provider_receives_the_recorded_source_sha(self) -> None:
        """The sha gives the enricher reproducibility claims: the call
        must carry it, so reruns against the same sha are stable."""
        from app.structure.service import StructureEnricher

        seen: list[tuple[str, str | None]] = []

        def tracking_provider(path: str, sha: str | None = None) -> str:
            seen.append((path, sha))
            if path not in SOURCES:
                raise KeyError(path)
            return SOURCES[path]

        incoming = [
            changed_file_record("httpx/_client.py", sha="q" * 40),
            doc_file_record(
                "docs/api.md", "# API\nThe timeout env HTTPX_TIMEOUT controls requests.\n"
            ),
        ]
        StructureEnricher(tracking_provider, LoggerSpy()).enrich(incoming)
        assert ("httpx/_client.py", "q" * 40) in seen


class TestEnrichFailureBoundary:
    def test_provider_error_is_a_loud_nonfatal_warning(self) -> None:
        from app.structure.service import StructureEnricher

        def exploding(path: str, sha: str | None = None) -> str:
            if path.endswith(".py"):
                raise RuntimeError("blob unavailable")
            raise KeyError(path)

        logger = LoggerSpy()
        incoming = [
            changed_file_record("httpx/_client.py"),
            changed_file_record("config/limits.toml"),
        ]
        plan = StructureEnricher(exploding, logger).enrich(incoming)
        labels = {n.record_id for n in plan.nodes}
        assert not any("httpx/_client.py" in rid for rid in labels)
        assert any(
            e[1].get("event") == "content_unavailable"
            and "httpx/_client.py" in e[1].get("path", "")
            for e in logger.warnings
        )

    def test_record_malformed_payload_does_not_crash_the_run(self) -> None:
        broken = {**corpus_records()[0]}
        del broken["payload"]["path"]
        plan, _ = run_enricher(records=[broken] + corpus_records()[1:])
        assert plan is not None

    def test_empty_corpus_yields_empty_plan(self) -> None:
        plan, _ = run_enricher(records=[])
        assert len(plan.nodes) == 0
        assert len(plan.rels) == 0


class TestEnrichPlanReusability:
    def test_plan_feeds_the_standard_builder(self) -> None:
        """Component isolation means the structure plan must load through
        the same GraphBuilder.load_plan contract as everything else."""
        plan, _ = run_enricher()
        from app.graph.builder import GraphBuilder

        session = FakeGraphSession()
        GraphBuilder(session=session).load_plan(plan, repo="encode/httpx")
        assert session.executed > 0


class TestSourceSanity:
    def test_fixture_snippets_carry_the_anchors_tests_expect(self) -> None:
        """If the fixture text drifts, span-based tests must fail loudly,
        not silently pass against old line numbers."""
        assert "class Client:" in CLIENT_PY
        assert "def _send_handling_redirects" in CLIENT_PY
        assert "def broken(:" in BROKEN_PY
