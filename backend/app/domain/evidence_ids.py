"""Evidence ID strategy: content-addressed, stable, repo-scoped.

IDs use the repository's short name (``httpx``), never ``encode/httpx``.
They are written into the graph and cited in answers, so they are treated
as a public format: change one and you orphan shipped edges.
"""

from __future__ import annotations


def _short(repo: str) -> str:
    short = repo.rstrip("/").split("/")[-1].strip()
    if not short:
        raise ValueError("repo must be a non-empty 'owner/name' or 'name'")
    return short


def _required(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def pull_request_id(*, repo: str, number: int) -> str:
    return f"{_short(repo)}:pr:{number}"


def commit_id(*, repo: str, sha: str) -> str:
    return f"{_short(repo)}:commit:{_required(sha, 'sha')}"


def review_id(*, repo: str, review_id: int) -> str:
    return f"{_short(repo)}:review:{review_id}"


def issue_id(*, repo: str, number: int) -> str:
    return f"{_short(repo)}:issue:{number}"


def person_id(*, repo: str, login: str) -> str:
    return f"{_short(repo)}:person:{_required(login, 'login')}"


def doc_id(*, repo: str, path: str) -> str:
    return f"{_short(repo)}:doc:{_required(path, 'path')}"


def review_comment_id(*, repo: str, comment_id: int) -> str:
    return f"{_short(repo)}:review_comment:{comment_id}"


def issue_comment_id(*, repo: str, comment_id: int) -> str:
    return f"{_short(repo)}:issue_comment:{comment_id}"


def pr_file_id(*, repo: str, number: int, path: str) -> str:
    return f"{_short(repo)}:pr:{number}:file:{_required(path, 'path')}"


def code_region_id(*, repo: str, path: str, start_line: int, line: int) -> str:
    return f"{_short(repo)}:region:{_required(path, 'path')}:{start_line}:{line}"


def repo_id(*, repo: str) -> str:
    return f"{_short(repo)}:repo"
