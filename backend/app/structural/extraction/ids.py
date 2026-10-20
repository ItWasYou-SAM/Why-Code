"""Deterministic structural identifiers.

Identity is ``path@commit_sha`` for files, symbols and regions. A symbol
from ``httpx/_client.py@aaa`` is never merged with the same name at
``bbb``: that is how the graph avoids mixing code from different points
in history.
"""

from __future__ import annotations


def short_repo(repo: str) -> str:
    short = repo.rstrip("/").split("/")[-1].strip()
    if not short:
        raise ValueError("repo must be a non-empty 'owner/name' or 'name'")
    return short


def module_name(path: str) -> str:
    """Dotted module name for a Python path.

    ``httpx/_transports/default.py`` -> ``httpx._transports.default``.
    Package initializers collapse to the package name; a root ``__init__.py``
    has no module component.
    """
    for suffix in (".pyi", ".py"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    if path == "__init__":
        return ""
    if path.endswith("/__init__"):
        path = path[: -len("/__init__")]
    return path.replace("/", ".")


def file_id(*, repo: str, path: str, commit_sha: str) -> str:
    return f"{short_repo(repo)}:file:{path}@{commit_sha}"


def symbol_id(*, repo: str, path: str, commit_sha: str, qualname: str) -> str:
    return f"{short_repo(repo)}:symbol:{path}@{commit_sha}:{qualname}"


def region_id(
    *, repo: str, path: str, commit_sha: str, start_line: int, end_line: int
) -> str:
    return f"{short_repo(repo)}:region:{path}@{commit_sha}:{start_line}:{end_line}"


def config_key_id(*, repo: str, source: str, key: str) -> str:
    return f"{short_repo(repo)}:config:{source}:{key}"


def extracted_import_id(
    *,
    file_id: str,
    line: int,
    module: str | None,
    name: str | None,
    alias: str | None,
) -> str:
    """One ID per imported binding, not per import statement."""
    return (
        f"{file_id}:import:{line}:{module or ''}:{name or ''}:{alias or ''}"
    )


def external_ref_id(*, repo: str, module: str) -> str:
    return f"{short_repo(repo)}:external:{module}"


def doc_node_id(*, repo: str, path: str) -> str:
    return f"{short_repo(repo)}:doc:{path}"
