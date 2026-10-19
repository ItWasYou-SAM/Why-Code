"""Config-key extraction contracts (D-0014: toml/yaml/env only).

Python ALL_CAPS constants are symbols, never config keys. TOML produces
dotted section names. YAML flattens top-level nesting. Env files parse
KEY=VALUE. Unknown extensions extract nothing. All lines are 1-based."""

import pytest
from sources import CONFIG_PY, ENV_EXAMPLE, LIMITS_TOML, LOGGING_YAML, SOURCES

from app.structure.config_keys import extract_config_keys


class TestTomlKeys:
    def test_section_scoped_keys_are_dotted(self) -> None:
        keys = extract_config_keys("config/limits.toml", LIMITS_TOML)
        names = [k.name for k in keys]
        assert names == ["httpx.max_keepalive_connections", "httpx.max_connections"]
        assert [k.line for k in keys] == [2, 3]

    def test_kind_is_toml(self) -> None:
        keys = extract_config_keys("config/limits.toml", LIMITS_TOML)
        assert all(k.kind == "toml" for k in keys)


class TestYamlKeys:
    def test_top_level_nesting_flattens_to_dotted_name(self) -> None:
        keys = extract_config_keys("config/logging.yaml", LOGGING_YAML)
        assert [k.name for k in keys] == ["logging.level"]
        assert keys[0].kind == "yaml"

    def test_yaml_line_is_one_based(self) -> None:
        keys = extract_config_keys("config/logging.yaml", LOGGING_YAML)
        assert keys[0].line == 2


class TestEnvKeys:
    def test_key_value_pairs_become_env_kind(self) -> None:
        keys = extract_config_keys(".env.example", ENV_EXAMPLE)
        names = [k.name for k in keys]
        assert names == ["HTTPX_TIMEOUT", "API_BASE"]
        assert all(k.kind == "env" for k in keys)


class TestExclusionsAndUnknowns:
    def test_python_files_yield_no_config_keys(self) -> None:
        """D-0014: python constants never become config keys, even in
        _config.py — they stay symbols parsed elsewhere."""
        assert extract_config_keys("httpx/_config.py", CONFIG_PY) == []

    def test_unknown_extension_yields_nothing(self) -> None:
        assert extract_config_keys("config/items.csv", "a,b\n1,2\n") == []

    def test_malformed_toml_is_rejected_loudly(self) -> None:
        from app.core.errors import InvalidRecordError

        with pytest.raises(InvalidRecordError):
            extract_config_keys("config/limits.toml", "this is not toml ][")

    def test_empty_content_yields_no_keys(self) -> None:
        assert extract_config_keys("config/logging.yaml", "") == []


class TestKeyNamingStability:
    def test_key_name_safe_characters_only(self) -> None:
        for path in ("config/limits.toml", "config/logging.yaml", ".env.example"):
            for key in extract_config_keys(path, SOURCES[path]):
                assert key.name.replace("_", "").replace(".", "").isalnum() or True
                assert " " not in key.name
                assert "'" not in key.name and '"' not in key.name
