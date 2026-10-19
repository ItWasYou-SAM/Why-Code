"""Python config module used as the fixture for httpx/_config.py."""

from __future__ import annotations

import os

DEFAULT_TIMEOUT_CONFIG = 5.0
DEFAULT_RETRIES = 0
HTTPX_TIMEOUT_ENV = "HTTPX_TIMEOUT"


def timeout_from_env() -> float:
    value = os.environ.get("HTTPX_TIMEOUT")
    if value is None:
        return DEFAULT_TIMEOUT_CONFIG
    return float(value)


class Timeout:
    def __init__(self, value: float | None = None) -> None:
        self.value = value if value is not None else DEFAULT_TIMEOUT_CONFIG
