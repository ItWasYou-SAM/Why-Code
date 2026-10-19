"""Reference extraction: query-driven, and shallow on purpose.

A REFERENCES edge is emitted only when the target is provably one of:

1. a symbol defined in the same parsed file,
2. a symbol imported in the same file whose defining file is known,
3. an attribute of a known imported module or class,
4. a simple local rule (``self.method`` / ``cls.method``).

Anything else is discarded. There is no type inference and no dynamic
dispatch: an unresolvable name is not a relationship.

Imported targets are carried as ``target_path`` + ``target_qualname`` because
one file cannot see another file's symbols. The batch resolver
(:func:`resolve_imported_references`) turns those into symbol IDs once every
selected file has been extracted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.structural.extraction.models import (
    ImportRef,
    Reference,
    StructuralFile,
    Symbol,
    SymbolKind,
)
from app.structural.languages.python_parser import run_query

_IGNORED = {
    "self",
    "cls",
    "super",
    "print",
    "len",
    "str",
    "int",
    "float",
    "bool",
    "list",
    "dict",
    "set",
    "tuple",
    "frozenset",
    "isinstance",
    "getattr",
    "setattr",
    "hasattr",
    "range",
    "enumerate",
    "zip",
    "open",
    "type",
    "object",
    "None",
    "True",
    "False",
    "Exception",
    "ValueError",
    "TypeError",
    "KeyError",
    "RuntimeError",
}

_SELF = {"self", "cls"}


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


@dataclass
class _ImportIndex:
    """Names this file binds through imports, and what they point at."""

    modules: dict[str, ImportRef] = field(default_factory=dict)
    symbols: dict[str, tuple[ImportRef, str]] = field(default_factory=dict)

    @classmethod
    def build(cls, imports: list[ImportRef]) -> _ImportIndex:
        index = cls()
        for imported in imports:
            if not imported.resolved_path:
                continue
            if imported.module:
                index.modules.setdefault(imported.module, imported)
            if imported.names:
                # ``from mod import Real as Local`` binds Local.
                for alias, real in imported.aliases.items():
                    index.symbols.setdefault(alias, (imported, real))
                for name in imported.names:
                    if name in imported.aliases.values():
                        continue
                    index.symbols.setdefault(name, (imported, name))
            else:
                # ``import mod as local`` binds the module under the alias.
                for alias in imported.aliases:
                    index.modules.setdefault(alias, imported)
        return index


def _enclosing_symbol(symbols: list[Symbol], line: int) -> Symbol | None:
    candidates = [
        symbol for symbol in symbols if symbol.start_line <= line <= symbol.end_line
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda symbol: (symbol.end_line - symbol.start_line, -symbol.start_line))
    return candidates[0]


class _Resolver:
    def __init__(
        self,
        *,
        path: str,
        symbols: list[Symbol],
        imports: list[ImportRef],
        known_symbols_by_name: dict[str, list[Symbol]] | None = None,
    ) -> None:
        self.path = path
        self.by_name: dict[str, list[Symbol]] = {}
        for symbol in symbols:
            self.by_name.setdefault(symbol.name, []).append(symbol)
        for name, extra in (known_symbols_by_name or {}).items():
            self.by_name.setdefault(name, []).extend(extra)
        self.imports = _ImportIndex.build(imports)

    def same_file(self, name: str) -> list[Symbol]:
        return [symbol for symbol in self.by_name.get(name, []) if symbol.path == self.path]

    def identifier(self, name: str) -> list[Reference]:
        same = self.same_file(name)
        if same:
            return [self._same_file(name, symbol) for symbol in same]
        imported = self.imports.symbols.get(name)
        if imported is not None:
            return [self._imported(name, imported)]
        return []

    def attribute(self, object_text: str, name: str) -> list[Reference]:
        same = self.same_file(name)
        if object_text in _SELF and same:
            return [self._same_file(name, symbol) for symbol in same]

        imported_class = self.imports.symbols.get(object_text)
        if imported_class is not None:
            imported, real = imported_class
            return [self._imported(name, (imported, f"{real}.{name}"))]

        imported_module = self.imports.modules.get(object_text)
        if imported_module is not None:
            return [self._imported(name, (imported_module, name))]

        # ``ClassName.method`` where the class is defined in this file.
        for class_symbol in self.same_file(object_text):
            if class_symbol.kind is not SymbolKind.CLASS:
                continue
            qualname = f"{class_symbol.qualname}.{name}"
            for target in self.by_name.get(name, []):
                if target.path == self.path and target.qualname == qualname:
                    return [self._same_file(name, target)]
        return []

    def _same_file(self, name: str, target: Symbol) -> Reference:
        return Reference(
            source_qualname="",
            target_symbol_id=target.symbol_id,
            name=name,
            line=1,
            resolution="same_file",
        )

    def _imported(self, name: str, imported: tuple[ImportRef, str]) -> Reference:
        reference, real = imported
        return Reference(
            source_qualname="",
            target_path=reference.resolved_path,
            target_qualname=real,
            name=name,
            line=1,
            resolution="imported",
        )


def extract_references(
    tree: Any,
    source: bytes,
    *,
    symbols: list[Symbol],
    imports: list[ImportRef] | None = None,
    path: str = "",
    known_symbols_by_name: dict[str, list[Symbol]] | None = None,
) -> list[Reference]:
    resolver = _Resolver(
        path=path,
        symbols=symbols,
        imports=imports or [],
        known_symbols_by_name=known_symbols_by_name,
    )
    references: list[Reference] = []
    seen: set[tuple[Any, ...]] = set()

    for _pattern, captures in run_query("python_references.scm", tree.root_node):
        name_node = _first(captures, "reference.name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        if not name or name in _IGNORED:
            continue
        line = int(name_node.start_point.row) + 1
        source_symbol = _enclosing_symbol(symbols, line)
        if source_symbol is None:
            continue

        object_node = _first(captures, "reference.object")
        if object_node is not None:
            attribute_node = _first(captures, "reference.attribute")
            if attribute_node is not None and _is_chain_link(attribute_node):
                continue
            object_text = _text(object_node, source)
            candidates = resolver.attribute(object_text, name)
        else:
            candidates = resolver.identifier(name)

        for candidate in candidates:
            if candidate.target_symbol_id == source_symbol.symbol_id:
                continue
            if candidate.target_path == path and candidate.target_symbol_id is None:
                continue
            resolved = candidate.model_copy(
                update={
                    "source_qualname": source_symbol.qualname,
                    "line": line,
                }
            )
            key = (
                resolved.source_qualname,
                resolved.name,
                resolved.line,
                resolved.target_symbol_id,
                resolved.target_path,
                resolved.target_qualname,
            )
            if key in seen:
                continue
            seen.add(key)
            references.append(resolved)

    references.sort(key=lambda ref: (ref.line, ref.source_qualname, ref.name))
    return references


def _first(captures: dict[str, list[Any]], key: str) -> Any | None:
    nodes = captures.get(key)
    return nodes[0] if nodes else None


def _is_chain_link(attribute_node: Any) -> bool:
    """True when this attribute is only a step in a longer access chain.

    In ``pkg.b.helper`` the inner ``pkg.b`` is the object of another
    attribute; in ``pkg.b.helper()`` the outer attribute is the function of
    a call. Neither is itself the thing being referenced, so emitting it
    would add a guessed edge.
    """
    parent = attribute_node.parent
    if parent is None:
        return False
    if parent.type == "attribute":
        object_node = parent.child_by_field_name("object")
        return object_node is not None and object_node.id == attribute_node.id
    if parent.type == "call":
        function_node = parent.child_by_field_name("function")
        return function_node is not None and function_node.id == attribute_node.id
    return False


def resolve_imported_references(files: list[StructuralFile]) -> None:
    """Turn cross-file references into symbol IDs, discarding the rest.

    Runs after every selected file is extracted, because only then does the
    batch know which symbols exist at which path. An imported name whose
    defining file was not selected (or does not define it) is dropped: the
    plan allows an unresolved reference to be discarded.
    """
    by_path_qualname: dict[tuple[str, str], str] = {}
    by_path_name: dict[tuple[str, str], str] = {}
    for entry in files:
        for symbol in entry.symbols:
            by_path_qualname.setdefault((entry.path, symbol.qualname), symbol.symbol_id)
            by_path_name.setdefault((entry.path, symbol.name), symbol.symbol_id)

    for entry in files:
        for reference in entry.references:
            if reference.target_symbol_id is not None:
                continue
            if not reference.target_path or not reference.target_qualname:
                continue
            target = by_path_qualname.get(
                (reference.target_path, reference.target_qualname)
            )
            if target is None:
                target = by_path_name.get(
                    (reference.target_path, reference.target_qualname.split(".")[-1])
                )
            if target is not None:
                reference.target_symbol_id = target
                reference.resolution = "imported"


__all__ = ["extract_references", "resolve_imported_references"]
