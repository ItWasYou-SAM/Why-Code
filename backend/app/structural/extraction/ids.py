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


def external_ref_id(*, repo: str, module: str) -> str:
    return f"{short_repo(repo)}:external:{module}"


def doc_node_id(*, repo: str, path: str) -> str:
    return f"{short_repo(repo)}:doc:{path}"
