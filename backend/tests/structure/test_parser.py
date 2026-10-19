"""Tree-sitter parser contracts (D-0013: syntactic only).

Spans are 1-based inclusive and asserted against the exact fixture
bytes. Qualnames are dotted. Constants are module-level ALL_CAPS
assignments only. Imports are module targets plus from-import names.
A syntax-error file parses to ok=False with zero symbols."""

from sources import BROKEN_PY, CLIENT_PY, CONFIG_PY

from app.structure.parser import parse_python, parse_python_with_diagnostics


class TestSymbols:
    def test_class_symbol_with_dotted_qualname(self) -> None:
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        client = [s for s in parsed.symbols if s.qualified_name == "Client"]
        assert len(client) == 1
        assert client[0].kind == "class"

    def test_method_nests_under_class_qualname(self) -> None:
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        method = [
            s
            for s in parsed.symbols
            if s.qualified_name == "Client._send_handling_redirects"
        ]
        assert len(method) == 1
        assert method[0].kind == "method"

    def test_top_level_constant_is_a_symbol_not_a_variable(self) -> None:
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        retries = [s for s in parsed.symbols if s.qualified_name == "DEFAULT_RETRIES"]
        assert retries and retries[0].kind == "constant"

    def test_lowercase_class_attribute_is_not_a_symbol(self) -> None:
        """`retry_count = 0` inside a class is noise; only ALL_CAPS
        module-level assignments qualify. Inventing lowercase 'symbols'
        would drown the decision graph."""
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        names = {s.qualified_name for s in parsed.symbols}
        assert "Client.retry_count" not in names

    def test_multiple_top_level_constants_in_config_file(self) -> None:
        parsed = parse_python("httpx/_config.py", CONFIG_PY)
        names = {s.qualified_name for s in parsed.symbols}
        assert names == {"DEFAULT_TIMEOUT_CONFIG", "DEFAULT_LIMITS"}
        assert all(s.kind == "constant" for s in parsed.symbols)
    def test_symbol_ids_are_composed_at_mapping_time_not_parser_time(self) -> None:
        """The parser deals in file-local qualnames; record ids like
        SYMBOL_ID values are the mapping layer's job. Keeping those
        separate means a parser change never silently renumbers ids."""
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        for symbol in parsed.symbols:
            assert "/" not in symbol.qualified_name


class TestSpans:
    def test_spans_are_one_based_and_ordering_well_formed(self) -> None:
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        for symbol in parsed.symbols:
            assert symbol.start_line >= 1
            assert symbol.end_line >= symbol.start_line

    def test_method_span_inside_class_span(self) -> None:
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        client = [s for s in parsed.symbols if s.qualified_name == "Client"][0]
        method = [
            s
            for s in parsed.symbols
            if s.qualified_name == "Client._send_handling_redirects"
        ][0]
        assert method.start_line >= client.start_line
        assert method.end_line <= client.end_line

    def test_constant_span_is_the_assignment_line(self) -> None:
        parsed = parse_python("httpx/_config.py", CONFIG_PY)
        timeout = [s for s in parsed.symbols if s.qualified_name == "DEFAULT_TIMEOUT_CONFIG"][0]
        assert timeout.start_line == timeout.end_line == 1


class TestImports:
    def test_module_import_collected(self) -> None:
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        assert any(i.module == "httpx" for i in parsed.imports)

    def test_from_import_keeps_module_and_names(self) -> None:
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        spec = [i for i in parsed.imports if i.module == "httpx._config"]
        assert len(spec) == 1
        assert set(spec[0].names) == {"DEFAULT_TIMEOUT_CONFIG", "DEFAULT_LIMITS"}

    def test_external_import_collected_as_plain_spec(self) -> None:
        parsed = parse_python("httpx/_client.py", CLIENT_PY)
        assert any(i.module == "ssl" for i in parsed.imports)


class TestUnparseable:
    def test_syntax_error_file_is_ok_false_with_zero_symbols(self) -> None:
        parsed = parse_python("broken/syntax.py", BROKEN_PY)
        assert parsed.ok is False
        assert parsed.symbols == []
        assert parsed.imports == []

    def test_diagnostics_surface_through_parse_with_diagnostics(self) -> None:
        parsed, diagnostics = parse_python_with_diagnostics("broken/syntax.py", BROKEN_PY)
        assert parsed.ok is False
        assert diagnostics, "silent failure hides bugs; diagnostics are mandatory"

    def test_clean_file_has_no_diagnostics(self) -> None:
        parsed, diagnostics = parse_python_with_diagnostics("httpx/_client.py", CLIENT_PY)
        assert parsed.ok is True
        assert diagnostics == []


class TestParsingDeterminism:
    def test_same_source_same_order(self) -> None:
        left = parse_python("httpx/_client.py", CLIENT_PY)
        right = parse_python("httpx/_client.py", CLIENT_PY)
        assert [(s.qualified_name, s.kind, s.start_line, s.end_line) for s in left.symbols] == [
            (s.qualified_name, s.kind, s.start_line, s.end_line) for s in right.symbols
        ]
