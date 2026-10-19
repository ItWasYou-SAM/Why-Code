"""Reference extraction: shallow, same-file only.

A reference edge is emitted only when the target resolves to a symbol
defined in the same parsed file (or to an imported symbol whose defining
file is in the same batch, handled by the graph builder). No type
inference, no dynamic dispatch, no cross-repo resolution.
"""

from __future__ import annotations

from typing import Any

from app.structural.extraction.models import Reference, Symbol

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


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _walk(node: Any) -> Any:
    yield node
    for child in node.children:
        yield from _walk(child)


def extract_references(
    tree: Any,
    source: bytes,
    *,
    symbols: list[Symbol],
    known_symbols_by_name: dict[str, list[Symbol]] | None = None,
) -> list[Reference]:
    by_name: dict[str, list[Symbol]] = {}
    for symbol in symbols:
        by_name.setdefault(symbol.name, []).append(symbol)
    for name, extra in (known_symbols_by_name or {}).items():
        by_name.setdefault(name, []).extend(extra)

    references: list[Reference] = []
    seen: set[tuple[str, str, int]] = set()

    for node in _walk(tree.root_node):
        if node.type != "call":
            continue
        function = node.child_by_field_name("function")
        if function is None:
            continue
        if function.type == "identifier":
            name = _text(function, source)
        elif function.type == "attribute":
            attribute = function.child_by_field_name("attribute")
            if attribute is None:
                continue
            name = _text(attribute, source)
        else:
            continue
        if name in _IGNORED or not name:
            continue
        targets = by_name.get(name, [])
        if not targets:
            continue
        line = int(node.start_point.row) + 1
        source_symbol = _enclosing_symbol(symbols, line)
        if source_symbol is None:
            continue
        for target in targets:
            if target.symbol_id == source_symbol.symbol_id:
                continue
            key = (source_symbol.symbol_id, target.symbol_id, line)
            if key in seen:
                continue
            seen.add(key)
            references.append(
                Reference(
                    source_qualname=source_symbol.qualname,
                    target_symbol_id=target.symbol_id,
                    name=name,
                    line=line,
                    resolution="same_file" if target.path == source_symbol.path else "imported",
                )
            )
    references.sort(key=lambda ref: (ref.line, ref.source_qualname, ref.target_symbol_id))
    return references


def _enclosing_symbol(symbols: list[Symbol], line: int) -> Symbol | None:
    candidates = [
        symbol for symbol in symbols if symbol.start_line <= line <= symbol.end_line
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda symbol: (symbol.end_line - symbol.start_line, -symbol.start_line))
    return candidates[0]
