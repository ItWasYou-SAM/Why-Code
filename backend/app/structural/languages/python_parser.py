"""Tree-sitter queries for Python definitions.

Queries live in ``queries/`` as data so the extraction grammar can be
audited without reading Python. Captures use the convention
``<thing>_def`` for the whole node and ``<thing>_name`` for its name.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

QUERY_DIR = Path(__file__).resolve().parent / "queries"


@cache
def load_query(name: str) -> str:
    path = QUERY_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"tree-sitter query not found: {path}")
    return path.read_text(encoding="utf-8")
