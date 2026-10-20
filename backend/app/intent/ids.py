"""Deterministic intent identifiers.

The LLM may propose a readable name, but identity is computed from the
validated claim and accepted evidence IDs. Replaying the same proposal over
the same evidence therefore produces the same node ID (D-0035).
"""

from __future__ import annotations

import hashlib
import re

_SLUG = re.compile(r"[^a-z0-9]+")


def short_repo(repo: str) -> str:
    short = repo.rstrip("/").split("/")[-1].strip()
    if not short:
        raise ValueError("repo must be a non-empty 'owner/name' or 'name'")
    return short


def slugify(text: str, *, max_length: int = 40) -> str:
    slug = _SLUG.sub("_", text.strip().lower()).strip("_")
    if not slug:
        return "item"
    return slug[:max_length].strip("_") or "item"


def intent_id(
    prefix: str,
    *,
    repo: str,
    claim: str,
    evidence_ids: list[str],
) -> str:
    digest = hashlib.sha256(
        "\x00".join(
            [claim.strip().lower(), *sorted(set(evidence_ids))]
        ).encode("utf-8")
    ).hexdigest()[:10]
    return f"{prefix}_{short_repo(repo)}_{slugify(claim)}_{digest}"


def incident_id(*, repo: str, issue_number: int) -> str:
    return f"inc_{short_repo(repo)}_{issue_number}"


def evidence_node_id(*, repo: str, evidence_id: str) -> str:
    digest = hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()[:16]
    return f"evi_{short_repo(repo)}_{digest}"


def review_item_id(*, repo: str, reason: str, pr_number: int | None, claim: str) -> str:
    digest = hashlib.sha256(
        "\x00".join(
            [reason, str(pr_number), claim.strip().lower()]
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"rq_{short_repo(repo)}_{digest}"


__all__ = [
    "evidence_node_id",
    "incident_id",
    "intent_id",
    "review_item_id",
    "short_repo",
    "slugify",
]
