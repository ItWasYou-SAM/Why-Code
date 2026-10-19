"""Config extraction contracts: declared keys and literal references only."""

from structural_helpers import fixture_text

from app.structural.extraction.config_extractor import (
    extract_config_keys,
    extract_config_references,
)
from app.structural.extraction.models import ConfigSourceKind, Language


class TestYaml:
    def keys(self):
        return extract_config_keys(
            fixture_text("sample_config.yaml"), path="config.yaml", language=Language.YAML
        )

    def test_nested_keys_are_dotted(self) -> None:
        keys = {key.key for key in self.keys()}
        assert {"default", "default.timeout", "default.retries.enabled"} <= keys

    def test_values_are_previewed_but_structures_are_not(self) -> None:
        by_key = {key.key: key for key in self.keys()}
        assert by_key["default.timeout"].value_preview == "5.0"
        assert by_key["default.retries"].value_preview == ""

    def test_source_kind_and_path_are_recorded(self) -> None:
        key = next(key for key in self.keys() if key.key == "logging.level")
        assert key.source_kind is ConfigSourceKind.YAML
        assert key.source_path == "config.yaml"
        assert key.line == 7


class TestOtherFormats:
    def test_toml(self) -> None:
        keys = extract_config_keys(
            "[tool.ruff]\nline-length = 100\n", path="pyproject.toml", language=Language.TOML
        )
        assert [key.key for key in keys] == ["tool", "tool.ruff", "tool.ruff.line-length"]

    def test_json(self) -> None:
        keys = extract_config_keys(
            '{"a": {"b": 1}}', path="package.json", language=Language.JSON
        )
        assert {key.key for key in keys} == {"a", "a.b"}

    def test_dotenv(self) -> None:
        keys = extract_config_keys(
            "# comment\nHTTPX_TIMEOUT=5\nexport HTTPX_DEBUG=1\n",
            path=".env",
            language=Language.ENV,
        )
        assert [key.key for key in keys] == ["HTTPX_TIMEOUT", "HTTPX_DEBUG"]

    def test_broken_yaml_yields_no_keys(self) -> None:
        assert extract_config_keys(
            "a: [unclosed\n", path="x.yaml", language=Language.YAML
        ) == []


class TestConfigReferences:
    def test_environment_lookup_is_found(self) -> None:
        refs = extract_config_references(
            'import os\nvalue = os.environ.get("HTTPX_TIMEOUT")\n',
            path="httpx/_client.py",
        )
        assert [(ref.key, ref.line) for ref in refs] == [("HTTPX_TIMEOUT", 2)]

    def test_subscript_and_getenv_are_found(self) -> None:
        refs = extract_config_references(
            'import os\n'
            'a = os.environ["HTTPX_DEBUG"]\n'
            'b = os.getenv("HTTPX_RETRIES")\n',
            path="httpx/_client.py",
        )
        assert {ref.key for ref in refs} == {"HTTPX_DEBUG", "HTTPX_RETRIES"}

    def test_reference_is_attributed_to_enclosing_symbol(self) -> None:
        source = (
            "def outer():\n"
            '    return os.getenv("HTTPX_TIMEOUT")\n'
        )
        refs = extract_config_references(
            source,
            path="httpx/_client.py",
            symbol_ranges=[("outer", 1, 2)],
        )
        assert refs[0].symbol_qualname == "outer"

    def test_uppercase_settings_attribute_is_found(self) -> None:
        refs = extract_config_references(
            "value = settings.HTTPX_RETRIES\n", path="httpx/_client.py"
        )
        assert refs[0].key == "HTTPX_RETRIES"

    def test_no_reference_is_invented_from_prose(self) -> None:
        refs = extract_config_references(
            "# HTTPX_TIMEOUT is documented elsewhere\n", path="httpx/_client.py"
        )
        assert refs == []
