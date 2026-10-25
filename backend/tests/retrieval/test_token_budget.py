"""Token estimation is a conservative upper bound, not a tokenizer."""

from __future__ import annotations

import pytest

from app.retrieval.context.token_budget import TokenBudget, estimate_tokens


def test_empty_text_is_zero_tokens() -> None:
    assert estimate_tokens("") == 0


def test_estimate_is_four_characters_per_token_rounded_up() -> None:
    assert estimate_tokens("a") == 1
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("abcde") == 2
    assert estimate_tokens("a" * 4000) == 1000


def test_fits_uses_used_tokens() -> None:
    budget = TokenBudget(10)
    assert budget.fits("a" * 20) is True
    assert budget.fits("a" * 24, used=5) is False


def test_remaining_never_goes_negative() -> None:
    budget = TokenBudget(10)
    assert budget.remaining() == 10
    assert budget.remaining(used=3) == 7
    assert budget.remaining(used=100) == 0


def test_short_text_is_not_truncated() -> None:
    budget = TokenBudget(10)
    assert budget.truncate("short text") == "short text"


def test_truncation_keeps_the_result_within_budget() -> None:
    budget = TokenBudget(5)
    result = budget.truncate("one two three four five six seven eight nine ten")
    assert estimate_tokens(result) <= 5
    assert result.endswith(" …")


def test_truncation_does_not_cut_mid_word() -> None:
    result = TokenBudget(3).truncate("alpha beta gamma delta epsilon")
    for word in result.removesuffix(" …").split():
        assert word in {"alpha", "beta", "gamma", "delta", "epsilon"}


def test_zero_budget_returns_empty() -> None:
    assert TokenBudget(5).truncate("anything", max_tokens=0) == ""


def test_negative_budget_is_rejected() -> None:
    with pytest.raises(ValueError):
        TokenBudget(0)
