"""The local quota guard: pacing, budgets, persistence, reconciliation.

No network: the inner provider is a fake and the clock is injectable, so
these tests pin the arithmetic that decides whether a real request is
allowed to leave the process.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.core.errors import QuotaExceededError, TransientUpstreamError
from app.intent.llm.limits import (
    DailyLedger,
    ProviderLimits,
    RateLimitedProvider,
    estimate_tokens,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


class FakeProvider:
    model = "openai/gpt-oss-20b"

    def __init__(
        self,
        *,
        max_output_tokens: int = 1000,
        usage: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.max_output_tokens = max_output_tokens
        self.last_usage = usage
        self.error = error
        self.calls = 0

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        del system, user, schema, schema_name
        self.calls += 1
        if self.error is not None:
            raise self.error
        return '{"ok": true}'


def build(
    tmp_path,
    *,
    limits: ProviderLimits | None = None,
    provider: FakeProvider | None = None,
    day: date = date(2026, 10, 24),
) -> tuple[RateLimitedProvider, FakeProvider, Clock, DailyLedger]:
    clock = Clock()
    ledger = DailyLedger(
        tmp_path / "llm_budget.json",
        key="openai/gpt-oss-20b",
        today=lambda: day,
    )
    inner = provider or FakeProvider()
    wrapper = RateLimitedProvider(
        inner,
        limits or ProviderLimits(min_interval_seconds=0.0),
        ledger,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    return wrapper, inner, clock, ledger


def call(wrapper: RateLimitedProvider) -> str:
    return wrapper.complete(
        system="system",
        user="user",
        schema={"type": "object"},
        schema_name="probe",
    )


def test_limits_reject_a_budget_above_the_provider_cap() -> None:
    with pytest.raises(ValueError, match="max_requests_per_day"):
        ProviderLimits(requests_per_day=10, max_requests_per_day=11)
    with pytest.raises(ValueError, match="max_tokens_per_day"):
        ProviderLimits(tokens_per_day=100, max_tokens_per_day=101)


def test_budget_helpers_fall_back_to_the_provider_cap() -> None:
    limits = ProviderLimits(
        requests_per_day=1000,
        tokens_per_day=200_000,
        max_requests_per_day=50,
    )
    assert limits.request_budget == 50
    assert limits.token_budget == 200_000


def test_calls_are_paced_to_the_per_minute_cap(tmp_path) -> None:
    wrapper, inner, clock, _ = build(
        tmp_path, limits=ProviderLimits(min_interval_seconds=2.0)
    )
    call(wrapper)
    clock.now = 0.5
    call(wrapper)
    assert inner.calls == 2
    assert clock.slept == [pytest.approx(1.5)]


def test_request_budget_refuses_before_the_second_call(tmp_path) -> None:
    wrapper, inner, _, ledger = build(
        tmp_path,
        limits=ProviderLimits(
            min_interval_seconds=0.0, max_requests_per_day=1
        ),
    )
    call(wrapper)
    with pytest.raises(QuotaExceededError, match="request budget exhausted"):
        call(wrapper)
    assert inner.calls == 1
    assert ledger.read().requests == 1


def test_token_budget_refuses_a_request_that_cannot_fit(tmp_path) -> None:
    wrapper, inner, _, ledger = build(
        tmp_path,
        limits=ProviderLimits(
            min_interval_seconds=0.0,
            tokens_per_day=200_000,
            max_tokens_per_day=100,
        ),
        provider=FakeProvider(max_output_tokens=500),
    )
    with pytest.raises(QuotaExceededError, match="token budget exhausted"):
        call(wrapper)
    assert inner.calls == 0
    assert ledger.read().requests == 0


def test_reservation_is_reconciled_with_reported_usage(tmp_path) -> None:
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    wrapper, _, _, ledger = build(
        tmp_path,
        limits=ProviderLimits(min_interval_seconds=0.0),
        provider=FakeProvider(usage=usage),
    )
    call(wrapper)
    recorded = ledger.read()
    assert recorded.requests == 1
    assert recorded.tokens == 120


def test_without_reported_usage_the_reservation_is_kept(tmp_path) -> None:
    wrapper, inner, _, ledger = build(
        tmp_path,
        limits=ProviderLimits(min_interval_seconds=0.0),
        provider=FakeProvider(usage=None, max_output_tokens=40),
    )
    call(wrapper)
    assert ledger.read().tokens > 40
    assert ledger.read().tokens >= estimate_tokens("system") + 40


def test_a_failed_call_counts_the_request_and_releases_the_output(tmp_path) -> None:
    wrapper, _, _, ledger = build(
        tmp_path,
        limits=ProviderLimits(min_interval_seconds=0.0),
        provider=FakeProvider(
            usage=None,
            max_output_tokens=1000,
            error=TransientUpstreamError("boom", component="intent"),
        ),
    )
    with pytest.raises(TransientUpstreamError):
        call(wrapper)
    recorded = ledger.read()
    assert recorded.requests == 1
    assert recorded.tokens < 1000


def test_a_single_request_larger_than_the_minute_cap_is_refused(tmp_path) -> None:
    wrapper, inner, _, _ = build(
        tmp_path,
        limits=ProviderLimits(
            min_interval_seconds=0.0,
            tokens_per_minute=100,
            tokens_per_day=200_000,
        ),
        provider=FakeProvider(max_output_tokens=500),
    )
    with pytest.raises(QuotaExceededError, match="per-minute"):
        call(wrapper)
    assert inner.calls == 0


def test_ledger_persists_and_rolls_over_at_utc_midnight(tmp_path) -> None:
    today = [date(2026, 10, 24)]
    ledger = DailyLedger(
        tmp_path / "ledger.json", key="model", today=lambda: today[0]
    )
    ledger.record(requests=1, tokens=100)
    assert ledger.read().requests == 1

    reloaded = DailyLedger(
        tmp_path / "ledger.json", key="model", today=lambda: today[0]
    )
    assert reloaded.read().tokens == 100

    today[0] = date(2026, 10, 25)
    assert reloaded.read().requests == 0
    reloaded.record(requests=1, tokens=7)
    assert reloaded.read().requests == 1
    assert reloaded.read().tokens == 7


def test_ledger_counters_are_per_model(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    DailyLedger(path, key="a").record(requests=3, tokens=30)
    DailyLedger(path, key="b").record(requests=1, tokens=10)
    assert DailyLedger(path, key="a").read().requests == 3
    assert DailyLedger(path, key="b").read().tokens == 10


def test_a_corrupt_ledger_reads_as_empty_instead_of_crashing(tmp_path) -> None:
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    assert DailyLedger(path, key="model").read().requests == 0
    DailyLedger(path, key="model").record(requests=1)
    assert DailyLedger(path, key="model").read().requests == 1


def test_estimate_tokens_is_positive_and_pessimistic() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 400) == 100
