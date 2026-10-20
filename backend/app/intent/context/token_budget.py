"""A tiny deterministic token budget.

The project does not ship a tokenizer dependency. A conservative
characters-per-token estimate is enough for packing and, crucially, is
stable across providers and test runs. Truncation always leaves a visible
marker so a reader knows the prompt was shortened.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

TRUNCATION_MARKER = "\n[... truncated ...]"


@dataclass(frozen=True)
class TokenBudget:
    max_tokens: int
    chars_per_token: float = 4.0

    def __post_init__(self) -> None:
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be at least 1")
        if self.chars_per_token <= 0:
            raise ValueError("chars_per_token must be positive")

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text) / self.chars_per_token))

    def fits(self, text: str) -> bool:
        return self.estimate(text) <= self.max_tokens

    def char_limit(self, tokens: int) -> int:
        return max(0, int(tokens * self.chars_per_token))

    def truncate(self, text: str, max_tokens: int | None = None) -> str:
        limit = self.max_tokens if max_tokens is None else max_tokens
        if limit < 1:
            return ""
        char_limit = self.char_limit(limit)
        if len(text) <= char_limit:
            return text
        if char_limit <= len(TRUNCATION_MARKER):
            return TRUNCATION_MARKER[:char_limit]
        return text[: char_limit - len(TRUNCATION_MARKER)] + TRUNCATION_MARKER

    def truncate_lines(self, lines: list[str], max_tokens: int) -> str:
        """Keep whole lines; never cut a target/evidence line in half."""
        limit = self.char_limit(max_tokens)
        kept: list[str] = []
        used = 0
        for line in lines:
            cost = len(line) + 1
            if used + cost > limit:
                break
            kept.append(line)
            used += cost
        if len(kept) < len(lines):
            kept.append("[... truncated ...]")
        return "\n".join(kept)


__all__ = ["TRUNCATION_MARKER", "TokenBudget"]
