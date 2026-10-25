"""Deterministic token budgeting.

The estimator is deliberately simple (four characters per token). It is not
a tokenizer; it is an upper-bound guard that keeps a long review thread from
crowding out the decision claim. The answer model's own tokenizer may differ
slightly, which is why the effective budget is conservative.
"""

from __future__ import annotations

import math


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


class TokenBudget:
    def __init__(self, max_tokens: int) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens

    def fits(self, text: str, *, used: int = 0) -> bool:
        return used + estimate_tokens(text) <= self.max_tokens

    def remaining(self, used: int = 0) -> int:
        return max(0, self.max_tokens - used)

    def truncate(self, text: str, *, max_tokens: int | None = None) -> str:
        limit = self.max_tokens if max_tokens is None else max_tokens
        if limit < 1:
            return ""
        if estimate_tokens(text) <= limit:
            return text
        # Four characters per token, then trim to a word boundary so the
        # prompt never ends mid-identifier. Reserve two characters for the
        # ellipsis so the result still fits the requested budget.
        character_budget = max(0, limit * 4 - 2)
        cut = text[:character_budget].rstrip()
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0].rstrip()
        if not cut:
            return ""
        return cut + " …"


__all__ = ["TokenBudget", "estimate_tokens"]
