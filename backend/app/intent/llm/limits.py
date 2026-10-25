"""Self-imposed LLM quotas.

A free/dev-tier provider publishes per-minute and per-day caps (Groq's dev
plan, for ``openai/gpt-oss-20b``: 30 requests/min, 1K requests/day, 8K
tokens/min, 200K tokens/day). Discovering a cap as a 429 is expensive: the
retry storm in :mod:`app.intent.llm.retry` can burn several requests, and a
forgotten test loop can consume the whole daily allowance, after which real
extraction runs are blocked for hours.

This module makes the published caps explicit and lets a run set a *lower*
self-imposed ceiling. :class:`RateLimitedProvider` then:

* paces calls so the per-minute request cap is never reached,
* waits for the token window to drain before crossing the per-minute cap,
* refuses (without sending anything) once the daily ceiling is reached,
* reserves usage before the HTTP call and reconciles it with the provider's
  reported ``usage`` afterwards, so a crashed process still counts.

Counters are persisted per model and UTC day in a JSON ledger, because the
intent CLI is a short-lived process: in-memory counters would reset on every
run and enforce nothing. The ledger is not a lock; two concurrent processes
can each see the same remaining budget. The intent pipeline runs one call at
a time, which is the case this protects.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.errors import QuotaExceededError

if TYPE_CHECKING:
    from app.intent.llm.provider import LLMProvider

# Rough English/code token estimate. Deliberately pessimistic: over-counting
# leaves headroom, under-counting silently blows the budget.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Cheap, provider-independent token estimate for pre-flight checks."""
    if not text:
        return 0
    return max(1, (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN)


@dataclass(frozen=True)
class ProviderLimits:
    """Published provider caps plus optional stricter local ceilings."""

    # Defaults are Groq's dev-plan limits for the gpt-oss models.
    requests_per_minute: int = 30
    requests_per_day: int = 1000
    tokens_per_minute: int = 8000
    tokens_per_day: int = 200_000
    # Minimum spacing between calls, in seconds. 30 RPM => 2s.
    min_interval_seconds: float = 2.0
    # Self-imposed daily ceilings; None means "use the whole provider cap".
    max_requests_per_day: int | None = None
    max_tokens_per_day: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "requests_per_minute",
            "requests_per_day",
            "tokens_per_minute",
            "tokens_per_day",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be positive")
        if self.min_interval_seconds < 0:
            raise ValueError("min_interval_seconds must not be negative")
        if self.max_requests_per_day is not None and not (
            0 < self.max_requests_per_day <= self.requests_per_day
        ):
            raise ValueError(
                "max_requests_per_day must be between 1 and requests_per_day"
            )
        if self.max_tokens_per_day is not None and not (
            0 < self.max_tokens_per_day <= self.tokens_per_day
        ):
            raise ValueError(
                "max_tokens_per_day must be between 1 and tokens_per_day"
            )

    @property
    def request_budget(self) -> int:
        return self.max_requests_per_day or self.requests_per_day

    @property
    def token_budget(self) -> int:
        return self.max_tokens_per_day or self.tokens_per_day


@dataclass
class Usage:
    requests: int = 0
    tokens: int = 0


def _utc_today() -> date:
    return datetime.now(UTC).date()


class DailyLedger:
    """Per-model request/token counters for one UTC day, on disk."""

    def __init__(
        self,
        path: Path | None,
        *,
        key: str,
        today: Callable[[], date] = _utc_today,
    ) -> None:
        self.path = path
        self.key = key
        self._today = today
        self._lock = threading.Lock()

    def read(self) -> Usage:
        with self._lock:
            return self._read_unlocked(self._today().isoformat())

    @property
    def day(self) -> str:
        """The UTC day the next read/write will be scoped to."""
        return self._today().isoformat()

    def record(self, *, requests: int = 0, tokens: int = 0) -> Usage:
        """Add to today's counters and persist. ``tokens`` may be negative.

        A negative delta reconciles an over-reservation; the stored value is
        clamped at zero so the file stays readable by humans.
        """
        day = self._today().isoformat()
        with self._lock:
            state = self._load()
            if state.get("day") != day:
                state = {"day": day, "models": {}}
            models = state.setdefault("models", {})
            entry = models.setdefault(self.key, {"requests": 0, "tokens": 0})
            entry["requests"] = max(0, int(entry.get("requests", 0)) + requests)
            entry["tokens"] = max(0, int(entry.get("tokens", 0)) + tokens)
            self._write(state)
            return Usage(requests=entry["requests"], tokens=entry["tokens"])

    def _read_unlocked(self, day: str) -> Usage:
        state = self._load()
        if state.get("day") != day:
            return Usage()
        entry = (state.get("models") or {}).get(self.key) or {}
        return Usage(
            requests=max(0, int(entry.get("requests", 0))),
            tokens=max(0, int(entry.get("tokens", 0))),
        )

    def _load(self) -> dict[str, Any]:
        if self.path is None or not self.path.exists():
            return {}
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt ledger must not wedge every run. Failing open here is
            # the lesser evil; the provider's own 429 is still the backstop.
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write(self, state: dict[str, Any]) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(self.path)


class RateLimitedProvider:
    """Pace, budget and account for calls made through ``inner``."""

    def __init__(
        self,
        inner: LLMProvider,
        limits: ProviderLimits,
        ledger: DailyLedger,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        max_minute_wait_seconds: float = 60.0,
    ) -> None:
        self.inner = inner
        self.limits = limits
        self.ledger = ledger
        self._sleep = sleep
        self._monotonic = monotonic
        self._max_minute_wait = max_minute_wait_seconds
        self._last_request_at: float | None = None
        self._window: list[tuple[float, int]] = []

    @property
    def model(self) -> str:
        return getattr(self.inner, "model", self.ledger.key)

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        prompt_tokens = (
            estimate_tokens(system)
            + estimate_tokens(user)
            + estimate_tokens(json.dumps(schema, separators=(",", ":")))
        )
        reserved_output = int(getattr(self.inner, "max_output_tokens", 2500))
        reservation = prompt_tokens + max(0, reserved_output)

        self._admit(reservation)
        # Reserve before the call: if this process dies mid-request, the
        # tokens were still billed by the provider.
        self.ledger.record(requests=1, tokens=reservation)

        try:
            text = self.inner.complete(
                system=system, user=user, schema=schema, schema_name=schema_name
            )
        except Exception:
            # The provider refused or the network failed; release the output
            # reservation we never spent but keep the request counted.
            self.ledger.record(tokens=-max(0, reserved_output))
            raise

        actual = _usage_tokens(getattr(self.inner, "last_usage", None))
        if actual is not None:
            self.ledger.record(tokens=actual - reservation)
        self._window.append((self._monotonic(), actual or reservation))
        return text

    def _admit(self, reservation: int) -> None:
        usage = self.ledger.read()
        if usage.requests + 1 > self.limits.request_budget:
            raise QuotaExceededError(
                f"daily LLM request budget exhausted for {self.model!r} "
                f"({usage.requests}/{self.limits.request_budget} on "
                f"{self.ledger.day}); reset {self.ledger.path} "
                "or raise WHYCODE_LLM_BUDGET_REQUESTS",
                component="intent",
            )
        if usage.tokens + reservation > self.limits.token_budget:
            raise QuotaExceededError(
                f"daily LLM token budget exhausted for {self.model!r} "
                f"({usage.tokens}/{self.limits.token_budget} used, "
                f"{reservation} needed); reset {self.ledger.path} or raise "
                "WHYCODE_LLM_BUDGET_TOKENS",
                component="intent",
            )
        if reservation + 1 > self.limits.tokens_per_minute:
            raise QuotaExceededError(
                f"one request needs ~{reservation} tokens but the per-minute "
                f"cap is {self.limits.tokens_per_minute}; shorten the prompt, "
                "lower WHYCODE_LLM_MAX_OUTPUT_TOKENS, or raise "
                "WHYCODE_LLM_TPM to the provider's published value",
                component="intent",
            )
        self._wait_for_minute_capacity(reservation)
        self._pace()

    def _pace(self) -> None:
        now = self._monotonic()
        if self._last_request_at is not None:
            wait = self.limits.min_interval_seconds - (now - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
                now = self._monotonic()
        self._last_request_at = now

    def _wait_for_minute_capacity(self, reservation: int) -> None:
        now = self._monotonic()
        self._window = [
            (at, tokens) for at, tokens in self._window if now - at < 60.0
        ]
        used = sum(tokens for _, tokens in self._window)
        if used + reservation <= self.limits.tokens_per_minute:
            return
        oldest = min(at for at, _ in self._window)
        wait = 60.0 - (now - oldest)
        if wait > self._max_minute_wait:
            raise QuotaExceededError(
                f"per-minute token cap reached ({used}/"
                f"{self.limits.tokens_per_minute}); next slot in {wait:.0f}s",
                component="intent",
            )
        self._sleep(max(0.0, wait))
        self._window = []


def _usage_tokens(usage: Any) -> int | None:
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if isinstance(total, int) and total >= 0:
            return total
    return None


__all__ = [
    "DailyLedger",
    "ProviderLimits",
    "RateLimitedProvider",
    "Usage",
    "estimate_tokens",
]
