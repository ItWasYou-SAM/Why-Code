"""Structure → plan mapping contracts (D-0014/D-0017).

Node ids are locked: symbols, regions, config keys, external refs. The
plan must reference existing corpus nodes (ChangedFile, Doc) by their
CANONICAL record ids from app.domain.evidence_ids — re-declaring them
with minimal props is a merge no-op (idempotent). USED_BY is
whole-word-mention based, deliberately string-level, not type
resolution. Everything is deterministic in source order."""


from sources import CONFIG_KEY_IDS, EXTERNAL_SSL_ID, SYMBOL_ID

from app.domain.evidence_ids import doc_id, pr_file_id


def make_plan():
    from fakes import corpus_records
    from sources import SOURCES

    from app.structure.mapping import ops_from_records

    def provider(path: str, sha: str | None = None) -> str:
        if path not in SOURCES:
            raise KeyError(path)
        return SOURCES[path]

    return ops_from_records(corpus_records(), content_provider=provider)


class TestSymbolIds:
    def test_symbol_record_ids_follow_the_locked_convention(self) -> None:
        plan = make_plan()
        symbols = [n for n in plan.nodes if n.label == "Symbol"]
        assert symbols
        for node in symbols:
            assert node.record_id.startswith("httpx:symbol:")
        assert SYMBOL_ID["client"] == "httpx:symbol:httpx/_client.py:Client"
        for locked in ("client", "redirects", "timeout"):
            assert any(n.record_id == SYMBOL_ID[locked] for n in symbols)

    def test_symbol_props_carry_kind_and_qualname(self) -> None:
        plan = make_plan()
        symbols = {n.props.get("qualified_name"): n for n in plan.nodes if n.label == "Symbol"}
        assert symbols["Client"].props["kind"] == "class"
        assert symbols["Client._send_handling_redirects"].props["kind"] == "method"
        assert symbols["DEFAULT_RETRIES"].props["kind"] == "constant"
        assert symbols["DEFAULT_LIMITS"].props["kind"] == "constant"

    def test_fixed_symbol_count(self) -> None:
        plan = make_plan()
        symbols = [n for n in plan.nodes if n.label == "Symbol"]
        assert len(symbols) == 5


class TestRegionLinking:
    def test_every_symbol_links_to_its_own_span_region(self) -> None:
        plan = make_plan()
        symbol_regions = [
            (r.from_record_id, r.to_record_id)
            for r in plan.rels
            if r.rel_type == "HAS_REGION" and r.from_record_id.startswith("httpx:symbol:")
        ]
        assert len(symbol_regions) == 5
        for _, to_id in symbol_regions:
            assert to_id.startswith("httpx:region:")

    def test_changed_file_links_to_symbol_regions(self) -> None:
        plan = make_plan()
        file_regions = [
            (r.from_record_id, r.to_record_id)
            for r in plan.rels
            if r.rel_type == "HAS_REGION" and r.from_record_id.startswith("httpx:pr:")
        ]
        assert len(file_regions) == 5

    def test_defines_edge_uses_canonical_changed_file_id(self) -> None:
        plan = make_plan()
        defines = [
            (r.from_record_id, r.to_record_id)
            for r in plan.rels
            if r.rel_type == "DEFINES" and r.from_record_id.startswith("httpx:pr:")
        ]
        client_id = pr_file_id(repo="encode/httpx", number=1131, path="httpx/_client.py")
        assert (client_id, SYMBOL_ID["client"]) in defines
        assert (client_id, SYMBOL_ID["redirects"]) in defines


class TestImportResolution:
    def test_intra_corpus_import_resolves_to_file_edge(self) -> None:
        plan = make_plan()
        init_id = pr_file_id("encode/httpx", 1131, "httpx/__init__.py")
        config_id = pr_file_id("encode/httpx", 1131, "httpx/_config.py")
        client_id = pr_file_id("encode/httpx", 1131, "httpx/_client.py")
        got = {(r.from_record_id, r.to_record_id) for r in plan.rels if r.rel_type == "IMPORTS"}
        assert got == {
            (client_id, init_id),  # import httpx → package __init__
            (client_id, config_id),  # from httpx._config import …
            (client_id, EXTERNAL_SSL_ID),  # import ssl → external
        }

    def test_external_import_becomes_externalref_node(self) -> None:
        plan = make_plan()
        node = [n for n in plan.nodes if n.record_id == EXTERNAL_SSL_ID]
        assert len(node) == 1
        assert node[0].label == "ExternalRef"

    def test_import_to_missing_corpus_file_creates_nothing(self) -> None:
        """Unresolvable repo-internal import = no node even guessing;
        honesty over completeness (plan acceptance)."""
        plan = make_plan()
        dangling = [r for r in plan.rels if "httpx/_unresolved" in r.to_record_id]
        assert dangling == []


class TestConfigKeys:
    def test_configkey_nodes_with_locked_ids(self) -> None:
        plan = make_plan()
        ids = {n.record_id for n in plan.nodes if n.label == "ConfigKey"}
        assert set(CONFIG_KEY_IDS.values()) <= ids

    def test_declares_edge_from_changed_file(self) -> None:
        plan = make_plan()
        declares = [
            (r.from_record_id, r.to_record_id) for r in plan.rels if r.rel_type == "DECLARES"
        ]
        toml_id = pr_file_id("encode/httpx", 1131, "config/limits.toml")
        assert (toml_id, CONFIG_KEY_IDS["keepalive"]) in declares

    def test_used_by_is_whole_word_mention_plus_same_file_symbols(self) -> None:
        plan = make_plan()
        used_by = [
            (r.from_record_id, r.to_record_id) for r in plan.rels if r.rel_type == "USED_BY"
        ]
        assert (CONFIG_KEY_IDS["keepalive"], SYMBOL_ID["limits"]) in used_by
        # "max_connections" must NOT link anything: its only occurrence is
        # inside the longer name max_keepalive_connections.
        maxconn_links = [
            pair for pair in used_by if pair[0] == CONFIG_KEY_IDS["maxconn"]
        ]
        assert maxconn_links == []

    def test_documented_by_by_mention_in_doc_content(self) -> None:
        plan = make_plan()
        documented = [
            (r.from_record_id, r.to_record_id) for r in plan.rels if r.rel_type == "DOCUMENTED_BY"
        ]
        assert (CONFIG_KEY_IDS["envtimeout"], doc_id("encode/httpx", "docs/api.md")) in documented
        other = doc_id("encode/httpx", "docs/other.md")
        assert (CONFIG_KEY_IDS["envtimeout"], other) not in documented


class TestPlanHygiene:
    def test_rel_endpoints_all_resolved_inside_plan(self) -> None:
        plan = make_plan()
        node_ids = {n.record_id for n in plan.nodes}
        for rel in plan.rels:
            assert rel.from_record_id in node_ids, rel
            assert rel.to_record_id in node_ids, rel

    def test_redeclared_corpus_nodes_carry_minimal_props(self) -> None:
        """D-0017: re-declarations set record_id/repo only. Even a
        buggy merge must not smuggle content anybody could overwrite."""
        plan = make_plan()
        docs = [n for n in plan.nodes if n.label == "Doc"]
        assert docs, "doc re-declaration missing"
        for doc in docs:
            assert set(doc.props) <= {"record_id", "repo"}

    def test_deterministic_source_ordering(self) -> None:
        left = make_plan()
        right = make_plan()
        assert [(n.label, n.record_id) for n in left.nodes] == [
            (n.label, n.record_id) for n in right.nodes
        ]
        assert [
            (r.rel_type, r.from_record_id, r.to_record_id) for r in left.rels
        ] == [(r.rel_type, r.from_record_id, r.to_record_id) for r in right.rels]


class TestCounts:
    def test_full_count_table(self) -> None:
        plan = make_plan()
        nodes: dict[str, int] = {}
        rels: dict[str, int] = {}
        for node in plan.nodes:
            nodes[node.label] = nodes.get(node.label, 0) + 1
        for rel in plan.rels:
            rels[rel.rel_type] = rels.get(rel.rel_type, 0) + 1
        assert nodes == {
            "Symbol": 5,
            "CodeRegion": 5,
            "ExternalRef": 1,
            "ConfigKey": 4,
            "Doc": 2,
            "ChangedFile": 6,
        }
        assert rels == {
            "DEFINES": 5,
            "HAS_REGION": 10,
            "IMPORTS": 3,
            "DECLARES": 4,
            "USED_BY": 1,
            "DOCUMENTED_BY": 1,
        }
