"""Sample Python fixture with the shapes the extractor must handle:
a decorated class, methods, an async function, constants, imports that
resolve and imports that do not, an environment lookup and config use.
"""

from __future__ import annotations

import os
from typing import Any

from httpx._config import DEFAULT_TIMEOUT_CONFIG, Timeout

DEFAULT_RETRIES = 0
RETRY_STATUS_CODES = frozenset({500, 502, 503, 504})


class Client:
    """A minimal client."""

    TIMEOUT_KEY = "HTTPX_TIMEOUT"

    def __init__(self, timeout: Any = None) -> None:
        self.timeout = timeout or DEFAULT_TIMEOUT_CONFIG

    @property
    def is_retryable(self) -> bool:
        return DEFAULT_RETRIES > 0

    def send(self, request: Any) -> Any:
        return self._send(request)

    async def _send(self, request: Any) -> Any:
        timeout = os.environ.get("HTTPX_TIMEOUT")
        return Timeout(timeout)


def build_client(timeout: Any = None) -> Client:
    return Client(timeout=timeout)
