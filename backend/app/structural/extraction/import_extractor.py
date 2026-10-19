"""Import extraction and *honest* resolution.

Extraction is query-driven: ``python_imports.scm`` captures each statement
and every name it binds. Resolution then happens only when the target can be
determined from the path alone:

* absolute ``httpx._config`` -> ``httpx/_config.py`` or package ``__init__``
* relative ``from .base import X`` -> sibling ``base.py``
* relative ``from . import _types`` -> ``_types.py``

Anything else (third-party packages, namespace tricks, dynamic imports)
is recorded as an ``ExternalRef`` and never guessed into a file edge.
"""

from __future__ import annotations

from typing import Any

from app.structural.extraction.models import ImportKind, ImportRef
from app.structural.languages.python_parser import run_query

_PY_SUFFIXES = (".py",)

_STATEMENT_CAPTURES = (
    "import.statement",
    "import.from_statement",
    "import.relative_from_statement",
)


def _source_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _first(captures: dict[str, list[Any]], key: str) -> Any | None:
    nodes = captures.get(key)
    return nodes[0] if nodes else None


def _relative_prefix(node: Any, source: bytes) -> tuple[int, str]:
    text = _source_text(node, source)
    level = len(text) - len(text.lstrip("."))
    remainder = text[level:]
    return level, remainder


def extract_imports(
    tree: Any, source: bytes, *, path: str, known_paths: set[str] | None = None
) -> list[ImportRef]:
    known = known_paths or set()
    imports: list[ImportRef] = []
    grouped: dict[int, dict[str, Any]] = {}

    for _pattern, captures in run_query("python_imports.scm", tree.root_node):
        statement = next(
            (node for key in _STATEMENT_CAPTURES if (node := _first(captures, key))),
            None,
        )
        if statement is None:
            continue
        if statement.type == "import_statement":
            name_node = _first(captures, "import.name")
            if name_node is None:
                continue
            alias_node = _first(captures, "import.alias")
            module = _source_text(name_node, source)
            aliases = (
                {_source_text(alias_node, source): module}
                if alias_node is not None
                else {}
            )
            imports.append(
                _make_ref(
                    module,
                    ImportKind.ABSOLUTE,
                    0,
                    [],
                    aliases,
                    False,
                    int(statement.start_point.row) + 1,
                    path,
                    known,
                )
            )
            continue

        group = grouped.setdefault(
            statement.id,
            {
                "node": statement,
                "module": "",
                "kind": ImportKind.ABSOLUTE,
                "level": 0,
                "names": [],
                "aliases": {},
                "wildcard": False,
            },
        )
        relative = _first(captures, "import.relative_module")
        absolute = _first(captures, "import.module")
        if relative is not None:
            level, remainder = _relative_prefix(relative, source)
            group["level"] = level
            group["module"] = remainder
            group["kind"] = ImportKind.RELATIVE
        elif absolute is not None:
            module_node = absolute
            if module_node.type == "relative_import":  # wildcard relative import
                level, remainder = _relative_prefix(module_node, source)
                group["level"] = level
                group["module"] = remainder
                group["kind"] = ImportKind.RELATIVE
            else:
                group["module"] = _source_text(module_node, source)
        name_node = _first(captures, "import.name")
        if name_node is not None:
            name = _source_text(name_node, source)
            group["names"].append(name)
            alias_node = _first(captures, "import.alias")
            if alias_node is not None:
                group["aliases"][_source_text(alias_node, source)] = name
        if _first(captures, "import.wildcard") is not None:
            group["wildcard"] = True

    for group in grouped.values():
        statement = group["node"]
        imports.append(
            _make_ref(
                group["module"],
                group["kind"],
                group["level"],
                group["names"],
                group["aliases"],
                group["wildcard"],
                int(statement.start_point.row) + 1,
                path,
                known,
            )
        )

    imports.sort(key=lambda ref: (ref.line, ref.module))
    return imports


def _make_ref(
    module: str,
    kind: ImportKind,
    level: int,
    names: list[str],
    aliases: dict[str, str],
    wildcard: bool,
    line: int,
    path: str,
    known: set[str],
) -> ImportRef:
    resolved_path, resolved_module = resolve_import(
        module=module,
        kind=kind,
        level=level,
        path=path,
        known_paths=known,
        names=names,
    )
    return ImportRef(
        module=module,
        kind=kind,
        level=level,
        names=names,
        aliases=aliases,
        wildcard=wildcard,
        line=line,
        resolved_path=resolved_path,
        resolved_module=resolved_module,
    )


def resolve_import(
    *,
    module: str,
    kind: ImportKind,
    level: int,
    path: str,
    known_paths: set[str],
    names: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Return (file_path, module_name) only when provably resolvable."""
    candidates: list[str] = []
    if kind is ImportKind.ABSOLUTE and module:
        base = module.replace(".", "/")
        candidates.extend([f"{base}.py", f"{base}/__init__.py"])
    elif kind is ImportKind.RELATIVE:
        package_dir = path.rsplit("/", 1)[0] if "/" in path else ""
        parts = package_dir.split("/") if package_dir else []
        up = max(level - 1, 0)
        if up > len(parts):
            return None, None
        base_parts = parts[: len(parts) - up] if up else parts
        remainder = module.replace(".", "/") if module else ""
        base = "/".join([*base_parts, remainder]) if remainder else "/".join(base_parts)
        if remainder:
            candidates.extend([f"{base}.py", f"{base}/__init__.py"])
        else:
            # ``from . import _types`` imports a sibling module, not the
            # package itself: prefer the named submodule when it exists.
            for name in names or []:
                candidates.append(f"{base}/{name}.py" if base else f"{name}.py")
            candidates.append(f"{base}/__init__.py" if base else "__init__.py")

    if not known_paths:
        return None, None
    for candidate in candidates:
        normalized = candidate.lstrip("/")
        if normalized in known_paths:
            return normalized, _module_name(normalized)
    return None, None


def _module_name(file_path: str) -> str:
    for suffix in _PY_SUFFIXES:
        if file_path.endswith(suffix):
            file_path = file_path[: -len(suffix)]
    if file_path.endswith("/__init__"):
        file_path = file_path[: -len("/__init__")]
    return file_path.replace("/", ".")


__all__ = ["extract_imports", "resolve_import"]
