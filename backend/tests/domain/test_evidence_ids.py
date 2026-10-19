"""Contract for evidence IDs: stable, repo-scoped, predictable strings.

These IDs are written into the graph, cited in answers, and shown to
judges. They cannot churn silently. If one of these tests breaks it is a
design change, not a refactor — it happens consciously in plan + test.
"""

from app.domain.evidence_ids import (
    commit_id,
    issue_comment_id,
    pr_file_id,
    pull_request_id,
    review_comment_id,
)


def test_pull_request_id() -> None:
    assert pull_request_id(repo="encode/httpx", number=1131) == "httpx:pr:1131"


def test_commit_id() -> None:
    assert commit_id(repo="encode/httpx", sha="abc123def") == "httpx:commit:abc123def"


def test_review_comment_id() -> None:
    assert review_comment_id(repo="encode/httpx", comment_id=456) == "httpx:review_comment:456"


def test_issue_comment_id() -> None:
    assert issue_comment_id(repo="encode/httpx", comment_id=789) == "httpx:issue_comment:789"


def test_pr_file_id_is_stable_for_same_pr_and_path() -> None:
    first = pr_file_id(repo="encode/httpx", number=1131, path="httpx/_client.py")
    second = pr_file_id(repo="encode/httpx", number=1131, path="httpx/_client.py")
    assert first == second == "httpx:pr:1131:file:httpx/_client.py"


def test_ids_never_contain_the_full_repo_slash_prefix() -> None:
    """Repo scope is the first token and is the short name (httpx), which
    keeps IDs compact in Cypher, graph path UI, and citations."""
    pr = pull_request_id(repo="encode/httpx", number=1)
    assert pr.split(":")[0] == "httpx"


def test_pr_file_id_rejects_empty_path() -> None:
    import pytest

    with pytest.raises(ValueError):
        pr_file_id(repo="encode/httpx", number=1, path="")
