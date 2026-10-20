"""Bounded retry for transient provider failures.

Only ``TransientUpstreamError`` and ``RateLimitError`` are retried. Schema
failures have their own corrective retry in the structured client; a 4xx
request error is never repeated.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from app.core.errors import RateLimitError, TransientUpstreamError

RETRYABLE = (TransientUpstreamError, RateLimitError)


def call_with_retry[T](
    operation: Callable[[], T],
    *,
    attempts: int = 2,
    base_delay: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    retry_on: tuple[type[Exception], ...] = RETRYABLE,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> T:
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except retry_on as error:
            last = error
            if attempt >= attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            if on_retry is not None:
                on_retry(attempt, error, delay)
            sleep(delay)
    # The loop either returns or raises; this keeps type checkers honest.
    raise last if last is not None else RuntimeError("retry operation failed")


__all__ = ["RETRYABLE", "call_with_retry"]
