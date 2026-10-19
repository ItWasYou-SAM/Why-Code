"""Record-to-op mapping rules.

The rules are deliberately conservative: a relationship is only invented
when the record itself names its target. Parent/container edges are added
only when the container node exists in the same corpus, so a partial
corpus (a single file in a unit test) does not manufacture dangling edges.
Explicit references — an issue number, a review-comment path — must
resolve; if they do not, the corpus is refused (D-0011).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.core.errors import InvalidRecordError
from app.domain.evidence_ids import (
    code_region_id,
    person_id,
    pull_request_id,
    repo_id,
)
from app.domain.evidence_ids import (
    issue_id as make_issue_id,
)
from app.domain.evidence_ids import (
    review_id as make_review_id,
)
from app.graph.ops import LABEL_BY_TYPE, GraphPlan, NodeOp, RelOp, _node

_KNOWN_TYPES = set(LABEL_BY_TYPE)


def _require(record: dict[str, Any], key: str, *, context: str) -> Any:
    if key not in record or record[key] in (None, ""):
        raise InvalidRecordError(
            f"missing required field {key!r}",
            component="graph",
            record_id=record.get("record_id") or context,
        )
    return record[key]


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise InvalidRecordError(
            "payload must be an object",
            component="graph",
            record_id=record.get("record_id"),
        )
    return payload


def _author(payload: dict[str, Any]) -> str | None:
    author = payload.get("author")
    return author if isinstance(author, str) and author.strip() else None


def _region_anchor(payload: dict[str, Any]) -> tuple[int, int] | None:
    raw_line = payload.get("line")
    raw_start = payload.get("start_line")
    line = raw_line if isinstance(raw_line, int) else None
    start = raw_start if isinstance(raw_start, int) else None
    if line is None and start is None:
        return None
    end_line = line if line is not None else start
    start_line = start if start is not None else line
    if start_line is None or end_line is None:
        return None
    if start_line > end_line:
        start_line, end_line = end_line, start_line
    return start_line, end_line


def ops_from_records(records: Iterable[dict[str, Any]]) -> GraphPlan:
    materialized = list(records)
    plan = GraphPlan()

    base: list[tuple[dict[str, Any], str]] = []
    seen: set[str] = set()

    for record in materialized:
        record_id = _require(record, "record_id", context="<unknown>")
        record_type = _require(record, "record_type", context=record_id)
        if record_type not in _KNOWN_TYPES:
            raise InvalidRecordError(
                f"unknown record_type {record_type!r}",
                component="graph",
                record_id=record_id,
            )
        if record_id in seen:
            continue
        seen.add(record_id)
        base.append((record, LABEL_BY_TYPE[record_type]))

    repo = _repo_of(materialized, base)
    person_ids = {
        person_id(repo=repo, login=str(_payload(r).get("login") or ""))
        for r, label in base
        if label == "Person"
    }

    for record, label in base:
        record_id = record["record_id"]
        payload = _payload(record)
        props = {key: value for key, value in payload.items() if key != "record_id"}
        plan.nodes.append(_node(label, record_id, props, repo))

    plan.nodes.insert(0, _node("Repo", repo_id(repo=repo), {"name": repo}, repo))

    by_type: dict[str, list[dict[str, Any]]] = {}
    for record, label in base:
        by_type.setdefault(label, []).append(record)

    def rel(
        rel_type: str,
        from_id: str,
        to_id: str,
        origin: str,
        props: dict[str, Any] | None = None,
    ) -> None:
        plan.rels.append(
            RelOp(
                rel_type=rel_type,
                from_record_id=from_id,
                to_record_id=to_id,
                props={"origin_record_id": origin, **(props or {})},
            )
        )

    prs = by_type.get("PullRequest", [])
    commits = by_type.get("Commit", [])
    files = by_type.get("ChangedFile", [])
    reviews = by_type.get("Review", [])
    review_comments = by_type.get("ReviewComment", [])
    issues = by_type.get("Issue", [])
    issue_comments = by_type.get("IssueComment", [])
    docs = by_type.get("Doc", [])

    pr_ids = {pr["record_id"] for pr in prs}
    file_by_pr_path = {
        (file.get("pr_number"), _payload(file).get("path")): file["record_id"]
        for file in files
    }
    issue_ids = {issue["record_id"] for issue in issues}

    # Repo owns every PR.
    for pr in prs:
        rel("HAS_PULL_REQUEST", repo_id(repo=repo), pr["record_id"], pr["record_id"])

    # PR contains commits, files, reviews and (by fallback) issues.
    def pr_of(record: dict[str, Any]) -> str | None:
        number = record.get("pr_number")
        if not isinstance(number, int):
            return None
        candidate = pull_request_id(repo=repo, number=number)
        return candidate if candidate in pr_ids else None

    for commit in commits:
        pr_id = pr_of(commit)
        if pr_id is not None:
            rel("HAS_COMMIT", pr_id, commit["record_id"], commit["record_id"])
    for file in files:
        pr_id = pr_of(file)
        if pr_id is not None:
            rel("CHANGED_FILE", pr_id, file["record_id"], file["record_id"])
    for review in reviews:
        pr_id = pr_of(review)
        if pr_id is not None:
            rel("HAS_REVIEW", pr_id, review["record_id"], review["record_id"])

    # Review comments: attach to the review named in the payload, else to
    # the only review of the PR. Ambiguous multi-review PRs stay unlinked
    # rather than guessed.
    review_ids = {review["record_id"] for review in reviews}
    reviews_by_pr: dict[int, list[dict[str, Any]]] = {}
    for review in reviews:
        if review.get("pr_number") is not None:
            reviews_by_pr.setdefault(review["pr_number"], []).append(review)

    for comment in review_comments:
        payload = _payload(comment)
        review_ref = payload.get("pull_request_review_id")
        target: str | None = None
        if review_ref is not None:
            candidate = make_review_id(repo=repo, review_id=int(review_ref))
            if candidate in review_ids:
                target = candidate
            else:
                raise InvalidRecordError(
                    f"review comment references unknown review {review_ref}",
                    component="graph",
                    record_id=comment["record_id"],
                )
        elif comment.get("pr_number") is not None:
            candidates = reviews_by_pr.get(comment["pr_number"], [])
            if len(candidates) == 1:
                target = candidates[0]["record_id"]
        if target is not None:
            rel("HAS_COMMENT", target, comment["record_id"], comment["record_id"])

    # Code regions come only from review-comment anchors (D-0010).
    for comment in review_comments:
        payload = _payload(comment)
        path = payload.get("path")
        anchor = _region_anchor(payload)
        if not path or anchor is None:
            continue
        start_line, end_line = anchor
        file_id = file_by_pr_path.get((comment.get("pr_number"), path))
        if file_id is None:
            raise InvalidRecordError(
                f"review comment anchors {path!r} but no changed file with that path "
                f"exists for the PR",
                component="graph",
                record_id=comment["record_id"],
            )
        region = code_region_id(
            repo=repo, path=str(path), start_line=start_line, line=end_line
        )
        plan.nodes.append(
            _node(
                "CodeRegion",
                region,
                {
                    "path": path,
                    "start_line": start_line,
                    "line": end_line,
                    "side": payload.get("side"),
                },
                repo,
            )
        )
        rel("HAS_REGION", file_id, region, comment["record_id"])
        rel("COMMENTS_ON", comment["record_id"], region, comment["record_id"])

    # Issues discussed by a PR.
    for issue in issues:
        pr_id = pr_of(issue)
        if pr_id is not None:
            rel("DISCUSSES", pr_id, issue["record_id"], issue["record_id"])

    # Issue comments: explicit issue number wins, PR is the fallback.
    for comment in issue_comments:
        payload = _payload(comment)
        issue_number = payload.get("issue_number")
        issue_target: str | None = None
        if issue_number is not None:
            issue_target = make_issue_id(repo=repo, number=int(issue_number))
            if issue_target not in issue_ids:
                raise InvalidRecordError(
                    f"issue comment references unknown issue {issue_number}",
                    component="graph",
                    record_id=comment["record_id"],
                )
        elif comment.get("pr_number") is not None:
            candidate = pull_request_id(repo=repo, number=comment["pr_number"])
            if candidate in pr_ids:
                issue_target = candidate
        if issue_target is None:
            raise InvalidRecordError(
                "issue comment has no issue number and no PR context",
                component="graph",
                record_id=comment["record_id"],
            )
        rel("HAS_COMMENT", issue_target, comment["record_id"], comment["record_id"])

    # Authorship edges only for authors present in the person set. An
    # author string is not a stable identity, so we never fabricate a
    # Person node from it.
    def person_for(author: str | None) -> str | None:
        if not author:
            return None
        candidate = person_id(repo=repo, login=author)
        return candidate if candidate in person_ids else None

    for pr in prs:
        actor = person_for(_author(_payload(pr)))
        if actor:
            rel("AUTHORED", actor, pr["record_id"], pr["record_id"])
    for commit in commits:
        actor = person_for(_author(_payload(commit)))
        if actor:
            rel("AUTHORED", actor, commit["record_id"], commit["record_id"])
    for review in reviews:
        actor = person_for(_author(_payload(review)))
        review_pr = pr_of(review)
        if actor and review_pr is not None:
            rel("REVIEWED", actor, review_pr, review["record_id"])
    for comment in [*review_comments, *issue_comments]:
        actor = person_for(_author(_payload(comment)))
        if actor:
            rel("COMMENTED", actor, comment["record_id"], comment["record_id"])

    # Doc nodes are declared but not linked yet; Component 3 owns the
    # DOCUMENTED_BY edge because it knows which symbol a doc describes.
    _ = docs

    plan.assert_integrity()
    return plan


def _repo_of(
    materialized: list[dict[str, Any]], base: list[tuple[dict[str, Any], str]]
) -> str:
    repos = {
        str(record["repo"])
        for record in materialized
        if isinstance(record.get("repo"), str) and record["repo"]
    }
    if len(repos) > 1:
        raise InvalidRecordError(
            f"one load corpus may only contain one repo, got {sorted(repos)}",
            component="graph",
        )
    if repos:
        return str(next(iter(repos)))
    if base:
        raise InvalidRecordError(
            "record is missing its repo scope",
            component="graph",
            record_id=base[0][0].get("record_id"),
        )
    raise InvalidRecordError("empty corpus has no repo", component="graph")


__all__ = ["ops_from_records", "NodeOp", "RelOp"]
