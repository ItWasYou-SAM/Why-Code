"""Import extraction and *honest* resolution.

Extraction reads exactly what is written. Resolution is attempted only
when the target can be determined from the path alone:

* absolute ``httpx._config`` -> ``httpx/_config.py`` or package ``__init__``
* relative ``from .base import X`` -> sibling ``base.py``
* relative ``from . import _types`` -> ``_types.py``

Anything else (third-party packages, namespace tricks, dynamic imports)
is recorded as an ``ExternalRef`` and never guessed into a file edge.
"""

from __future__ import annotations

from typing import Any

from app.structural.extraction.models import ImportKind, ImportRef

_PY_SUFFIXES = (".py",)


def _source_text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


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
    for node in _walk(tree.root_node):
        if node.type == "import_statement":
            imports.extend(_plain_imports(node, source, path, known))
        elif node.type == "import_from_statement":
            imports.append(_from_import(node, source, path, known))
    imports.sort(key=lambda ref: (ref.line, ref.module))
    return imports


def _walk(node: Any) -> Any:
    yield node
    for child in node.children:
        yield from _walk(child)


def _plain_imports(
    node: Any, source: bytes, path: str, known: set[str]
) -> list[ImportRef]:
    results: list[ImportRef] = []
    line = int(node.start_point.row) + 1
    for child in node.children:
        if child.type == "dotted_name":
            module = _source_text(child, source)
            results.append(
                _make_ref(module, ImportKind.ABSOLUTE, 0, [], {}, False, line, path, known)
            )
        elif child.type == "aliased_import":
            name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if name_node is None:
                continue
            module = _source_text(name_node, source)
            aliases = (
                {_source_text(alias_node, source): module} if alias_node is not None else {}
            )
            results.append(
                _make_ref(
                    module, ImportKind.ABSOLUTE, 0, [], aliases, False, line, path, known
                )
            )
    return results


def _from_import(node: Any, source: bytes, path: str, known: set[str]) -> ImportRef:
    line = int(node.start_point.row) + 1
    module_node = node.child_by_field_name("module_name")
    module = ""
    level = 0
    kind = ImportKind.ABSOLUTE
    if module_node is not None:
        if module_node.type == "relative_import":
            level, remainder = _relative_prefix(module_node, source)
            module = remainder
            kind = ImportKind.RELATIVE
        else:
            module = _source_text(module_node, source)

    names: list[str] = []
    aliases: dict[str, str] = {}
    wildcard = False
    module_id = module_node.id if module_node is not None else None
    for child in node.children:
        if child.id == module_id:
            continue
        if child.type == "dotted_name":
            names.append(_source_text(child, source))
        elif child.type == "aliased_import":
            name_node = child.child_by_field_name("name")
            alias_node = child.child_by_field_name("alias")
            if name_node is not None:
                name = _source_text(name_node, source)
                names.append(name)
                if alias_node is not None:
                    aliases[_source_text(alias_node, source)] = name
        elif child.type == "wildcard_import":
            wildcard = True

    return _make_ref(module, kind, level, names, aliases, wildcard, line, path, known)


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
