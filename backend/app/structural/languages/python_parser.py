"""Tree-sitter queries for Python.

Queries live in ``queries/`` as data so the extraction grammar can be
audited without reading Python. The single language object and the compiled
queries are cached: constructing them is the only version-sensitive step.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

QUERY_DIR = Path(__file__).resolve().parent / "queries"


@cache
def load_query(name: str) -> str:
    path = QUERY_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"tree-sitter query not found: {path}")
    return path.read_text(encoding="utf-8")


@cache
def python_language() -> Any:
    import tree_sitter_python
    from tree_sitter import Language as TsLanguage

    return TsLanguage(tree_sitter_python.language())


@cache
def python_query(name: str) -> Any:
    from tree_sitter import Query

    return Query(python_language(), load_query(name))


def run_query(name: str, root: Any) -> list[tuple[int, dict[str, list[Any]]]]:
    """Run a named query over a parsed tree.

    Returns ``(pattern_index, {capture_name: [nodes]})`` pairs. ``matches``
    (rather than ``captures``) keeps a definition and its ``@name`` in the
    same tuple, which is what makes capture-driven extraction safe.
    """
    from tree_sitter import QueryCursor

    return list(QueryCursor(python_query(name)).matches(root))


__all__ = ["QUERY_DIR", "load_query", "python_language", "python_query", "run_query"]
