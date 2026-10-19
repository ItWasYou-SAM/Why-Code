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
from typing import Any

from app.structural.extraction.models import (
    ConfigKey,
    ConfigReference,
    ConfigSourceKind,
    Language,
)

_ENV_ACCESS = re.compile(
    r"os\.(?:environ(?:\.get)?\s*[\[(]|getenv\s*\()\s*[\"']([A-Za-z_][A-Za-z0-9_]*)[\"']"
)
_SETTINGS_ATTR = re.compile(r"\b(?:settings|config|conf|options)\.([A-Z][A-Z0-9_]*)\b")


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


def _preview(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return ""
    text = str(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _yaml_keys(source: str, path: str) -> list[ConfigKey]:
    import yaml

    try:
        document = yaml.safe_load(source)
    except yaml.YAMLError:
        return []
    keys: list[ConfigKey] = []

    def walk(node: Any, prefix: str, depth: int) -> None:
        if not isinstance(node, dict):
            return
        for raw_key, value in node.items():
            key = str(raw_key)
            full = f"{prefix}.{key}" if prefix else key
            keys.append(
                ConfigKey(
                    key=full,
                    source_path=path,
                    source_kind=ConfigSourceKind.YAML,
                    line=_find_line(source, key),
                    value_preview=_preview(value),
                )
            )
            if isinstance(value, dict) and depth < 8:
                walk(value, full, depth + 1)

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
            keys.append(
                ConfigKey(
                    key=full,
                    source_path=path,
                    source_kind=ConfigSourceKind.TOML,
                    line=_find_line(source, key),
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
            keys.append(
                ConfigKey(
                    key=full,
                    source_path=path,
                    source_kind=ConfigSourceKind.JSON,
                    line=_find_line(source, key),
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
        keys.append(
            ConfigKey(
                key=key,
                source_path=path,
                source_kind=ConfigSourceKind.ENV_FILE,
                line=number,
                value_preview=_preview(value.strip().strip("\"'")),
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

    for number, line in enumerate(source.splitlines(), start=1):
        for match in _ENV_ACCESS.finditer(line):
            key = match.group(1)
            if (key, number) in seen:
                continue
            seen.add((key, number))
            references.append(
                ConfigReference(
                    key=key,
                    source_kind=ConfigSourceKind.ENVIRONMENT,
                    path=path,
                    line=number,
                    symbol_qualname=enclosing(number),
                )
            )
        for match in _SETTINGS_ATTR.finditer(line):
            key = match.group(1)
            if (key, number) in seen:
                continue
            seen.add((key, number))
            references.append(
                ConfigReference(
                    key=key,
                    source_kind=ConfigSourceKind.ENVIRONMENT,
                    path=path,
                    line=number,
                    symbol_qualname=enclosing(number),
                )
            )
        for key in sorted(declared):
            quoted = f"'{key}'" in line or f'"{key}"' in line
            if len(key) < 3 or not quoted or (key, number) in seen:
                continue
            seen.add((key, number))
            references.append(
                ConfigReference(
                    key=key,
                    source_kind=ConfigSourceKind.ENVIRONMENT,
                    path=path,
                    line=number,
                    symbol_qualname=enclosing(number),
                )
            )
    references.sort(key=lambda ref: (ref.line, ref.key))
    return references


def _find_line(source: str, key: str) -> int | None:
    pattern = re.compile(rf"^\s*[\"']?{re.escape(key)}[\"']?\s*[:=]", re.MULTILINE)
    match = pattern.search(source)
    return source[: match.start()].count("\n") + 1 if match else None
