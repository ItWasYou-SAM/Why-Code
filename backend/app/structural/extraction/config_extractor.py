"""Config extraction for YAML, TOML, JSON, .env and environment lookups.

Declared keys come from parsing the config file itself. Referenced keys
come only from literal, recognizable access patterns in Python:

* ``os.environ["KEY"]`` / ``os.environ.get("KEY")``
* ``os.getenv("KEY")``
* ``settings.KEY`` where ``KEY`` is an uppercase identifier
* string literals that exactly match a declared config key

No key is invented from comments, prose, or fuzzy matching.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from app.structural.extraction.models import (
    ConfigKey,
    ConfigReference,
    ConfigSourceKind,
    Language,
)

_ENV_ACCESS = re.compile(
    r"os\.(?:environ(?:\.get)?\s*[\[(]|getenv\s*\()\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
)
_CONFIG_BASE = r"(?:settings|config|conf|options)"
_SETTINGS_ATTR = re.compile(
    rf"\b{_CONFIG_BASE}\.([A-Z][A-Z0-9_]*)\b(?!\.)"
)
_SETTINGS_CHAIN = re.compile(
    rf"\b{_CONFIG_BASE}((?:\.(?:[A-Za-z_][A-Za-z0-9_]*)){{2,}})"
)
_SETTINGS_SUBSCRIPT = re.compile(
    rf"\b{_CONFIG_BASE}\s*((?:\[\s*[\"'][^\"'\n]+[\"']\s*\])+)"
)
_SUBSCRIPT_TOKEN = re.compile(r"\[\s*[\"']([^\"'\n]+)[\"']\s*\]")


def extract_config_keys(
    source: str, *, path: str, language: Language
) -> list[ConfigKey]:
    if language is Language.YAML:
        return _yaml_keys(source, path)
    if language is Language.TOML:
        return _toml_keys(source, path)
    if language is Language.JSON:
        return _json_keys(source, path)
    if language is Language.ENV:
        return _env_keys(source, path)
    return []


def _value_and_type(value: Any) -> tuple[str | None, str | None]:
    """Literal text and a stable type name for a parsed config value."""
    if isinstance(value, bool):
        return ("true" if value else "false"), "bool"
    if value is None:
        return None, "null"
    if isinstance(value, int):
        return str(value), "int"
    if isinstance(value, float):
        return str(value), "float"
    if isinstance(value, str):
        return value, "str"
    if isinstance(value, dict):
        return None, "dict"
    if isinstance(value, list):
        return None, "list"
    return str(value), type(value).__name__


def _preview_text(value: str | None) -> str:
    if not value:
        return ""
    return value if len(value) <= 80 else value[:77] + "..."


def _preview(value: Any) -> str:
    text, _ = _value_and_type(value)
    return _preview_text(text)


def _yaml_value(node: Any) -> tuple[str | None, str | None]:
    """Read a composed YAML node as literal text plus its YAML type."""
    import yaml

    if isinstance(node, yaml.MappingNode):
        return None, "dict"
    if isinstance(node, yaml.SequenceNode):
        return None, "list"
    if isinstance(node, yaml.ScalarNode):
        tag = node.tag.rsplit(":", 1)[-1]
        if tag == "null":
            return None, "null"
        if tag == "bool":
            return node.value.lower(), "bool"
        known = {"int": "int", "float": "float", "str": "str"}
        return node.value, known.get(tag, tag or "str")
    return None, None


def _yaml_keys(source: str, path: str) -> list[ConfigKey]:
    """Walk the composed YAML tree so every key keeps its own source line.

    ``safe_load`` loses positions, and searching for a bare key finds the
    first same-named key anywhere in the document. Compose keeps the line of
    the exact mapping entry, including repeated nested names.
    """
    import yaml

    try:
        document = yaml.compose(source)
    except yaml.YAMLError:
        return []
    keys: list[ConfigKey] = []

    def walk(node: Any, prefix: str, depth: int) -> None:
        if not isinstance(node, yaml.MappingNode) or depth > 8:
            return
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                continue
            key = str(key_node.value)
            full = f"{prefix}.{key}" if prefix else key
            value, value_type = _yaml_value(value_node)
            keys.append(
                ConfigKey(
                    key=full,
                    source_path=path,
                    source_kind=ConfigSourceKind.YAML,
                    line=key_node.start_mark.line + 1,
                    value=value,
                    value_type=value_type,
                    value_preview=_preview_text(value),
                )
            )
            if isinstance(value_node, yaml.MappingNode):
                walk(value_node, full, depth + 1)

    walk(document, "", 0)
    return keys


def _toml_keys(source: str, path: str) -> list[ConfigKey]:
    import tomllib

    try:
        document = tomllib.loads(source)
    except tomllib.TOMLDecodeError:
        return []
    keys: list[ConfigKey] = []

    def walk(node: Any, prefix: str, depth: int) -> None:
        if not isinstance(node, dict):
            return
        for raw_key, value in node.items():
            key = str(raw_key)
            full = f"{prefix}.{key}" if prefix else key
            text, value_type = _value_and_type(value)
            keys.append(
                ConfigKey(
                    key=full,
                    source_path=path,
                    source_kind=ConfigSourceKind.TOML,
                    line=_find_line(source, key),
                    value=text,
                    value_type=value_type,
                    value_preview=_preview(value),
                )
            )
            if isinstance(value, dict) and depth < 8:
                walk(value, full, depth + 1)

    walk(document, "", 0)
    return keys


def _json_keys(source: str, path: str) -> list[ConfigKey]:
    try:
        document = json.loads(source)
    except json.JSONDecodeError:
        return []
    keys: list[ConfigKey] = []

    def walk(node: Any, prefix: str, depth: int) -> None:
        if not isinstance(node, dict):
            return
        for raw_key, value in node.items():
            key = str(raw_key)
            full = f"{prefix}.{key}" if prefix else key
            text, value_type = _value_and_type(value)
            keys.append(
                ConfigKey(
                    key=full,
                    source_path=path,
                    source_kind=ConfigSourceKind.JSON,
                    line=_find_line(source, key),
                    value=text,
                    value_type=value_type,
                    value_preview=_preview(value),
                )
            )
            if isinstance(value, dict) and depth < 8:
                walk(value, full, depth + 1)

    walk(document, "", 0)
    return keys


def _env_keys(source: str, path: str) -> list[ConfigKey]:
    keys: list[ConfigKey] = []
    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        text = value.strip().strip("\"'")
        keys.append(
            ConfigKey(
                key=key,
                source_path=path,
                source_kind=ConfigSourceKind.ENV_FILE,
                line=number,
                value=text,
                value_type="str",
                value_preview=_preview_text(text),
            )
        )
    return keys


def extract_config_references(
    source: str,
    *,
    path: str,
    declared_keys: set[str] | None = None,
    symbol_ranges: list[tuple[str, int, int]] | None = None,
) -> list[ConfigReference]:
    """Literal config access, attributed to the innermost enclosing symbol."""
    declared = declared_keys or set()
    references: list[ConfigReference] = []
    seen: set[tuple[str, int]] = set()

    def enclosing(line: int) -> str | None:
        candidates = [
            (qualname, start, end)
            for qualname, start, end in (symbol_ranges or [])
            if start <= line <= end
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[2] - item[1], item[1]))
        return candidates[0][0]

    def add(
        key: str,
        line: int,
        *,
        kind: Literal["environment", "config"] = "environment",
    ) -> None:
        if not key or (key, line) in seen:
            return
        seen.add((key, line))
        references.append(
            ConfigReference(
                key=key,
                source_kind=ConfigSourceKind.ENVIRONMENT,
                path=path,
                line=line,
                symbol_qualname=enclosing(line),
                kind=kind,
            )
        )

    for number, line in enumerate(source.splitlines(), start=1):
        for match in _ENV_ACCESS.finditer(line):
            add(match.group(1), number)

        # ``settings["retry"]["max_attempts"]`` -> ``retry.max_attempts``.
        for match in _SETTINGS_SUBSCRIPT.finditer(line):
            tokens = _SUBSCRIPT_TOKEN.findall(match.group(1))
            if not tokens or any("." in token or not token.strip() for token in tokens):
                continue
            add(".".join(token.strip() for token in tokens), number, kind="config")

        # ``config.retry.max_attempts`` -> ``retry.max_attempts``. A trailing
        # call (``config.retry.get("x")``) is not a literal key read; drop it
        # rather than claim an uncertain connection.
        for match in _SETTINGS_CHAIN.finditer(line):
            parts = match.group(1).lstrip(".").split(".")
            if line[match.end() : match.end() + 1] == "(":
                parts = parts[:-1]
            if len(parts) >= 2:
                add(".".join(parts), number, kind="config")

        # Single uppercase attributes: ``settings.HTTPX_TIMEOUT``.
        for match in _SETTINGS_ATTR.finditer(line):
            add(match.group(1), number, kind="config")

        # A string literal that exactly matches a declared key, e.g.
        # ``resolve("retry.max_attempts")``.
        for key in sorted(declared):
            if len(key) < 3:
                continue
            quoted = f"'{key}'" in line or f'"{key}"' in line
            if quoted:
                add(key, number, kind="config")

    references.sort(key=lambda ref: (ref.line, ref.key))
    return references


def _find_line(source: str, key: str) -> int | None:
    pattern = re.compile(rf"^\s*[\"']?{re.escape(key)}[\"']?\s*[:=]", re.MULTILINE)
    match = pattern.search(source)
    return source[: match.start()].count("\n") + 1 if match else None
