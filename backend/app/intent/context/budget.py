"""Per-category token budgets for the evidence pack.

The Phase 2 plan gives each kind of evidence a range rather than a single
number. A fixed table cannot honour both a small debugging budget and the
normal 6k-10k request, so the allocator water-fills the available budget in
proportion to category weights while respecting each category's floor and
ceiling. The result is deterministic: the same ``total`` and the same set of
present categories always produce the same allocation.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class CategorySpec:
    name: str
    weight: float
    min_tokens: int
    max_tokens: int


# Weights sum to 1.0. The ranges mirror the Phase 2 budget table: diff hunks
# 1k-2.4k, review comments 1.5k-3k, commit messages 0.5k-1k, issue context
# 1k-2k, PR description 0.3k-0.8k, docs 0.3k-1k, target catalogue 0.2k-0.8k.
CATEGORY_SPECS: tuple[CategorySpec, ...] = (
    CategorySpec("diff", 0.36, 1000, 2400),
    CategorySpec("review_comments", 0.26, 1500, 3000),
    CategorySpec("commit_messages", 0.12, 500, 1000),
    CategorySpec("pr_description", 0.06, 300, 800),
    CategorySpec("issue_context", 0.11, 1000, 2000),
    CategorySpec("docs", 0.05, 300, 1000),
    CategorySpec("targets", 0.04, 200, 800),
)

_SPECS_BY_NAME = {spec.name: spec for spec in CATEGORY_SPECS}


def allocate_budget(total: int, present: Iterable[str]) -> dict[str, int]:
    """Return a per-category allocation whose sum is at most ``total``.

    Categories not named in ``present`` receive no budget. When the budget is
    smaller than the sum of the floors (a deliberately tiny test budget), the
    floors are ignored and the allocation stays proportional.
    """
    names = {name for name in present if name in _SPECS_BY_NAME}
    specs = [spec for spec in CATEGORY_SPECS if spec.name in names]
    if total <= 0 or not specs:
        return {}

    total = int(total)
    use_floors = total >= sum(spec.min_tokens for spec in specs)
    remaining = total
    allocation: dict[str, int] = {}
    active = list(specs)

    while active:
        weight_sum = sum(spec.weight for spec in active)
        if weight_sum <= 0:
            break
        fixed = False
        for spec in list(active):
            share = remaining * spec.weight / weight_sum
            floor = spec.min_tokens if use_floors else 0
            if share < floor:
                allocation[spec.name] = floor
                remaining -= floor
                active.remove(spec)
                fixed = True
            elif share > spec.max_tokens:
                allocation[spec.name] = spec.max_tokens
                remaining -= spec.max_tokens
                active.remove(spec)
                fixed = True
        if remaining <= 0:
            break
        if not fixed:
            weight_sum = sum(spec.weight for spec in active)
            for spec in active:
                allocation[spec.name] = int(
                    remaining * spec.weight / weight_sum
                )
            leftover = total - sum(allocation.values())
            for spec in sorted(active, key=lambda item: (-item.weight, item.name)):
                if leftover <= 0:
                    break
                room = spec.max_tokens - allocation.get(spec.name, 0)
                added = min(room, leftover)
                allocation[spec.name] = allocation.get(spec.name, 0) + added
                leftover -= added
            break

    return {name: max(0, value) for name, value in allocation.items()}


__all__ = ["CATEGORY_SPECS", "CategorySpec", "allocate_budget"]
