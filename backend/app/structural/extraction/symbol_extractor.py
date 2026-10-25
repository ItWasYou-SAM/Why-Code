"""Symbol extraction: classes, functions, methods, constants.

Definitions come from the ``python_definitions.scm`` query, so the grammar is
data rather than a hand-written walk of node types. Hierarchy is computed by
walking each definition's ancestors to the nearest enclosing captured
definition; indentation is never consulted.

A decorated definition's region starts at its first decorator so the
declaration is covered whole.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Any

from app.structural.extraction.ids import symbol_id
from app.structural.extraction.models import Symbol, SymbolKind
from app.structural.languages.python_parser import run_query

_CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Type aliases are named like types, not like constants. ``URL = str`` stays a
# constant (it is SCREAMING_CASE); ``UserId = int`` and ``HTTPStatus = ...``
# are aliases. Lowercase assignments are ordinary variables and are skipped.
_TYPE_ALIAS_NAME = re.compile(r"^[A-Z][A-Za-z0-9_]*$")

# ``@x.setter`` / ``@x.getter`` / ``@x.deleter`` define a *second* symbol
# with the same bare name as the property. The decorator is explicit, so we
# record the role rather than letting two definitions collide on one ID.
_ACCESSOR_ROLE = re.compile(r"\.(setter|getter|deleter)$")

_DEFINITION_KINDS = {
    "definition.function": SymbolKind.FUNCTION,
    "definition.class": SymbolKind.CLASS,
    "definition.constant": SymbolKind.CONSTANT,
    "definition.type": SymbolKind.TYPE,
}

# A simple alias is a name bound directly to a type expression. Calls,
# lambdas and arbitrary expressions are not aliases, and comprehensions are
# never definitions.
_TYPE_EXPRESSION_NODES = {
    "identifier",
    "attribute",
    "subscript",
    "tuple",
    "list",
    "parenthesized_expression",
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


def _docstring(node: Any, source: bytes) -> str | None:
    """The first string literal in a function/class body, if present."""
    body = node.child_by_field_name("body")
    if body is None or body.named_child_count == 0:
        return None
    first = body.named_child(0)
    if first is None or first.type != "expression_statement":
        return None
    expression = first.named_child(0)
    if expression is None or expression.type != "string":
        return None
    try:
        value = ast.literal_eval(_text(expression, source))
    except (SyntaxError, ValueError):
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _is_local(node: Any) -> bool:
    """True when the node sits inside a function or lambda body.

    Constants and aliases are only extracted from module and class scope.
    Local assignments are implementation detail, not graph structure.
    """
    ancestor = node.parent
    while ancestor is not None:
        if ancestor.type in {"function_definition", "lambda"}:
            return True
        ancestor = ancestor.parent
    return False


def _is_type_factory_call(value_node: Any, source: bytes) -> bool:
    """Type variables are not constants and are not simple aliases."""
    if value_node is None or value_node.type != "call":
        return False
    function = value_node.child_by_field_name("function")
    if function is None:
        return False
    return _text(function, source) in {
        "TypeVar",
        "ParamSpec",
        "TypeVarTuple",
        "NewType",
    }


def _is_simple_type_alias(name: str, value_node: Any, source: bytes) -> bool:
    """Whether an assignment binds a name to a type expression."""
    if (
        value_node is None
        or _CONSTANT_NAME.match(name)
        or not _TYPE_ALIAS_NAME.match(name)
    ):
        return False
    node_type = value_node.type
    if node_type == "binary_operator":
        # PEP 604 unions: ``int | None``. Reject other binary expressions
        # because ``Flags = A | B`` is not provably a type alias.
        return "|" in _text(value_node, source)
    if node_type == "string":
        inner = _text(value_node, source).strip("'\"").strip()
        return bool(_TYPE_ALIAS_NAME.match(inner.rsplit(".", 1)[-1]))
    return node_type in _TYPE_EXPRESSION_NODES


def _collect_definitions(tree: Any, source: bytes) -> list[_Definition]:
    found: dict[int, _Definition] = {}
    for _pattern, captures in run_query("python_definitions.scm", tree.root_node):
        name_node = _first(captures, "name")
        if name_node is None:
            continue
        name = _text(name_node, source)
        value_node = _first(captures, "value")
        for key, kind in _DEFINITION_KINDS.items():
            node = _first(captures, key)
            if node is None or node.id in found:
                continue
            if kind is SymbolKind.CONSTANT:
                if _is_local(node):
                    continue
                # A lambda is an expression, never a named definition.
                if value_node is not None and value_node.type == "lambda":
                    continue
                if _is_type_factory_call(value_node, source):
                    continue
                if _is_simple_type_alias(name, value_node, source):
                    kind = SymbolKind.TYPE
                elif not _CONSTANT_NAME.match(name):
                    continue
            elif kind is SymbolKind.TYPE and _is_local(node):
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
                docstring=_docstring(definition.node, source),
            )
        )

    symbols.sort(key=lambda symbol: (symbol.start_line, symbol.qualname))
    return symbols


__all__ = ["extract_symbols"]
