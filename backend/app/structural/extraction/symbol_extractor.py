"""Symbol extraction: classes, functions, methods, constants.

Hierarchy comes from the syntax tree, never from indentation heuristics.
A decorated definition's region starts at its first decorator so the
declaration is covered whole.
"""

from __future__ import annotations

import re
from typing import Any

from app.structural.extraction.ids import symbol_id
from app.structural.extraction.models import Symbol, SymbolKind

_CONSTANT_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")

# ``@x.setter`` / ``@x.getter`` / ``@x.deleter`` define a *second* symbol
# with the same bare name as the property. The decorator is explicit, so we
# record the role rather than letting two definitions collide on one ID.
_ACCESSOR_ROLE = re.compile(r"\.(setter|getter|deleter)$")


def _accessor_role(decorators: list[str]) -> str | None:
    for decorator in decorators:
        match = _ACCESSOR_ROLE.search(decorator)
        if match:
            return match.group(1)
    return None



def _text(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


class _SymbolWalker:
    def __init__(self, source: bytes, *, repo: str, path: str, commit_sha: str) -> None:
        self.source = source
        self.repo = repo
        self.path = path
        self.commit_sha = commit_sha
        self.symbols: list[Symbol] = []

    def walk_module(self, root: Any) -> list[Symbol]:
        self._walk_body(root, parent_qualname=None, parent_is_class=False)
        self.symbols.sort(key=lambda symbol: (symbol.start_line, symbol.qualname))
        return self.symbols

    def _walk_body(self, node: Any, *, parent_qualname: str | None, parent_is_class: bool) -> None:
        for child in node.children:
            if child.type == "decorated_definition":
                definition = child.child_by_field_name("definition")
                if definition is not None:
                    self._visit_definition(
                        definition,
                        declaration=child,
                        parent_qualname=parent_qualname,
                        parent_is_class=parent_is_class,
                    )
            elif child.type in {"class_definition", "function_definition"}:
                self._visit_definition(
                    child,
                    declaration=child,
                    parent_qualname=parent_qualname,
                    parent_is_class=parent_is_class,
                )
            elif child.type == "expression_statement":
                self._maybe_constant(child, parent_qualname=parent_qualname)
            else:
                # Descend through blocks, if/else, try, with, etc.
                self._walk_body(
                    child,
                    parent_qualname=parent_qualname,
                    parent_is_class=parent_is_class,
                )

    def _visit_definition(
        self,
        definition: Any,
        *,
        declaration: Any,
        parent_qualname: str | None,
        parent_is_class: bool,
    ) -> None:
        name_node = definition.child_by_field_name("name")
        if name_node is None:
            return
        name = _text(name_node, self.source)
        qualname = f"{parent_qualname}.{name}" if parent_qualname else name
        decorators = self._decorators(declaration)
        # A property accessor is a distinct definition of the same name; the
        # role suffix keeps its ID unique without inventing hierarchy.
        role = _accessor_role(decorators)
        identity = f"{qualname}:{role}" if role else qualname
        if definition.type == "class_definition":
            kind = SymbolKind.CLASS
        elif parent_is_class:
            kind = SymbolKind.METHOD
        else:
            kind = SymbolKind.FUNCTION
        self.symbols.append(
            Symbol(
                symbol_id=symbol_id(
                    repo=self.repo,
                    path=self.path,
                    commit_sha=self.commit_sha,
                    qualname=identity,
                ),
                name=name,
                qualname=qualname,
                kind=kind,
                path=self.path,
                commit_sha=self.commit_sha,
                start_line=int(declaration.start_point.row) + 1,
                end_line=int(definition.end_point.row) + 1,
                parent_qualname=parent_qualname,
                decorators=decorators,
                is_async=any(child.type == "async" for child in definition.children),
            )
        )
        body = definition.child_by_field_name("body")
        if body is not None:
            self._walk_body(
                body,
                parent_qualname=qualname,
                parent_is_class=kind is SymbolKind.CLASS,
            )

    def _maybe_constant(self, statement: Any, *, parent_qualname: str | None) -> None:
        assignment = next(
            (child for child in statement.children if child.type == "assignment"), None
        )
        if assignment is None:
            return
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "identifier":
            return
        name = _text(left, self.source)
        if not _CONSTANT_NAME.match(name):
            return
        qualname = f"{parent_qualname}.{name}" if parent_qualname else name
        self.symbols.append(
            Symbol(
                symbol_id=symbol_id(
                    repo=self.repo, path=self.path, commit_sha=self.commit_sha, qualname=qualname
                ),
                name=name,
                qualname=qualname,
                kind=SymbolKind.CONSTANT,
                path=self.path,
                commit_sha=self.commit_sha,
                start_line=int(statement.start_point.row) + 1,
                end_line=int(statement.end_point.row) + 1,
                parent_qualname=parent_qualname,
            )
        )

    def _decorators(self, declaration: Any) -> list[str]:
        if declaration.type != "decorated_definition":
            return []
        return [
            _text(child, self.source).lstrip("@").strip()
            for child in declaration.children
            if child.type == "decorator"
        ]


def extract_symbols(
    tree: Any, source: bytes, *, repo: str, path: str, commit_sha: str
) -> list[Symbol]:
    return _SymbolWalker(source, repo=repo, path=path, commit_sha=commit_sha).walk_module(
        tree.root_node
    )
