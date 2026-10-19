"""Error taxonomy. Shared by every component; no component defines its own.

Every error carries optional ``component`` and ``record_id`` context so a
single log line is enough to find the offending artifact. The class names
are pinned by tests because they appear in logs and rendered answers.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for every cross-component error."""

    def __init__(
        self,
        message: str,
        *,
        component: str | None = None,
        record_id: str | None = None,
    ) -> None:
        self.message = message
        self.component = component
        self.record_id = record_id
        super().__init__(message)

    def __str__(self) -> str:
        context = []
        if self.component:
            context.append(f"component={self.component}")
        if self.record_id:
            context.append(f"record_id={self.record_id}")
        suffix = f" ({', '.join(context)})" if context else ""
        return f"{self.message}{suffix}"


class TransientUpstreamError(AppError):
    """A remote system failed in a way that may succeed on retry."""


class RateLimitError(AppError):
    """An upstream API refused the request because a quota was exhausted."""


class InvalidRecordError(AppError):
    """A record is malformed, or violates an integrity rule."""


class GraphWriteError(AppError):
    """A graph write failed or produced an invalid plan."""


class InsufficientEvidenceError(AppError):
    """There is not enough defensible evidence to answer or assert a claim."""
