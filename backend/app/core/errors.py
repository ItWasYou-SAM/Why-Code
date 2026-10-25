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
        endpoint: str | None = None,
    ) -> None:
        self.message = message
        self.component = component
        self.record_id = record_id
        # Endpoint is populated by the GitHub client so a run report can say
        # *which* call failed, not merely that the PR failed.
        self.endpoint = endpoint
        super().__init__(message)

    def __str__(self) -> str:
        context = []
        if self.component:
            context.append(f"component={self.component}")
        if self.record_id:
            context.append(f"record_id={self.record_id}")
        if self.endpoint:
            context.append(f"endpoint={self.endpoint}")
        suffix = f" ({', '.join(context)})" if context else ""
        return f"{self.message}{suffix}"


class TransientUpstreamError(AppError):
    """A remote system failed in a way that may succeed on retry."""


class RateLimitError(AppError):
    """An upstream API refused the request because a quota was exhausted."""


class QuotaExceededError(AppError):
    """A locally enforced quota would be crossed; no request was sent.

    Distinct from :class:`RateLimitError`, which means the provider already
    refused a call. This one is raised by our own budget guard and is
    therefore never retried.
    """


class InvalidRecordError(AppError):
    """A record is malformed, or violates an integrity rule."""


class GraphWriteError(AppError):
    """A graph write failed or produced an invalid plan."""


class InsufficientEvidenceError(AppError):
    """There is not enough defensible evidence to answer or assert a claim."""
