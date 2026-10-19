"""Symbol extraction: classes, functions, methods, constants.

Definitions come from the ``python_definitions.scm`` query, so the grammar is
data rather than a hand-written walk of node types. Hierarchy is computed by
walking each definition's ancestors to the nearest enclosing captured
definition; indentation is never consulted.

A decorated definition's region starts at its first decorator so the
declaration is covered whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.structural.extraction.ids import symbol_id
from app.structural.extraction.models import Symbol, SymbolKind
from app.structural.languages.python_parser import run_query

_CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

# ``@x.setter`` / ``@x.getter`` / ``@x.deleter`` define a *second* symbol
# with the same bare name as the property. The decorator is explicit, so we
# record the role rather than letting two definitions collide on one ID.
_ACCESSOR_ROLE = re.compile(r"\.(setter|getter|deleter)$")

_DEFINITION_KINDS = {
    "definition.function": SymbolKind.FUNCTION,
    "definition.class": SymbolKind.CLASS,
    "definition.constant": SymbolKind.CONSTANT,
}


@dataclass
class _Definition:
    node: Any
    kind: SymbolKind
    name: str
    declaration: Any
    decorators: list[str]
    is_async: bool
    qualname: str = ""
    parent_qualname: str | None = None

    @property
    def identity(self) -> str:
        role = _accessor_role(self.decorators)
        return f"{self.qualname}:{role}" if role else self.qualname

    @property
    def start_line(self) -> int:
        return int(self.declaration.start_point.row) + 1

    @property
    def end_line(self) -> int:
        return int(self.node.end_point.row) + 1


def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _first(captures: dict[str, list[Any]], key: str) -> Any | None:
    nodes = captures.get(key)
    return nodes[0] if nodes else None


def _accessor_role(decorators: list[str]) -> str | None:
    for decorator in decorators:
        match = _ACCESSOR_ROLE.search(decorator)
        if match:
            return match.group(1)
    return None


def _decorators(declaration: Any, source: bytes) -> list[str]:
    if declaration.type != "decorated_definition":
        return []
    return [
        _text(child, source).lstrip("@").strip()
        for child in declaration.children
        if child.type == "decorator"
    ]


def _collect_definitions(tree: Any, source: bytes) -> list[_Definition]:
    found: dict[int, _Definition] = {}
    for _pattern, captures in run_query("python_definitions.scm", tree.root_node):
        name_node = _first(captures, "name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        for key, kind in _DEFINITION_KINDS.items():
            node = _first(captures, key)
            if node is None or node.id in found:
                continue
            # Only SCREAMING_CASE assignments are constants; every other
            # assignment is ordinary code.
            if kind is SymbolKind.CONSTANT and not _CONSTANT_NAME.match(name):
                continue
            parent = node.parent
            declaration = (
                parent
                if parent is not None and parent.type == "decorated_definition"
                else node
            )
            found[node.id] = _Definition(
                node=node,
                kind=kind,
                name=name,
                declaration=declaration,
                decorators=_decorators(declaration, source),
                is_async=any(child.type == "async" for child in node.children),
            )
    # Parents start before their children, so one pass assigns qualnames in
    # an order where the parent is always resolved first.
    return sorted(found.values(), key=lambda definition: definition.node.start_byte)


def _parent_definition(
    definition: _Definition, by_node: dict[int, _Definition]
) -> _Definition | None:
    ancestor = definition.node.parent
    while ancestor is not None:
        if ancestor.id in by_node:
            return by_node[ancestor.id]
        ancestor = ancestor.parent
    return None


def extract_symbols(
    tree: Any, source: bytes, *, repo: str, path: str, commit_sha: str
) -> list[Symbol]:
    definitions = _collect_definitions(tree, source)
    by_node = {definition.node.id: definition for definition in definitions}
    symbols: list[Symbol] = []

    for definition in definitions:
        parent = _parent_definition(definition, by_node)
        if parent is not None:
            definition.parent_qualname = parent.qualname
        definition.qualname = (
            f"{definition.parent_qualname}.{definition.name}"
            if definition.parent_qualname
            else definition.name
        )
        kind = definition.kind
        if kind is SymbolKind.FUNCTION and parent is not None:
            kind = SymbolKind.METHOD if parent.kind is SymbolKind.CLASS else kind
        symbols.append(
            Symbol(
                symbol_id=symbol_id(
                    repo=repo,
                    path=path,
                    commit_sha=commit_sha,
                    qualname=definition.identity,
                ),
                name=definition.name,
                qualname=definition.qualname,
                kind=kind,
                path=path,
                commit_sha=commit_sha,
                start_line=definition.start_line,
                end_line=definition.end_line,
                parent_qualname=definition.parent_qualname,
                decorators=definition.decorators,
                is_async=definition.is_async,
            )
        )

    symbols.sort(key=lambda symbol: (symbol.start_line, symbol.qualname))
    return symbols


__all__ = ["extract_symbols"]
