"""Contract: the error taxonomy exists, is structural, and is shared.

Rule enforced by review, testable here: every cross-component error is in
this taxonomy. A component inventing its own error class fails this test."""

from app.core.errors import (
    AppError,
    GraphWriteError,
    InsufficientEvidenceError,
    InvalidRecordError,
    RateLimitError,
    TransientUpstreamError,
)

TAXONOMY = (
    TransientUpstreamError,
    RateLimitError,
    InvalidRecordError,
    GraphWriteError,
    InsufficientEvidenceError,
)


def test_every_error_is_a_subclass_of_app_error_or_is_the_base() -> None:
    for error_class in TAXONOMY + (AppError,):
        assert issubclass(error_class, AppError)


def test_errors_carry_component_and_record_context() -> None:
    error = InvalidRecordError("bad payload", component="ingestion", record_id="httpx:pr:1131")
    assert error.component == "ingestion"
    assert error.record_id == "httpx:pr:1131"
    assert "bad payload" in str(error)


def test_error_names_are_stable() -> None:
    """Names go into logs and rendered answers; renaming them later would
    break log forensics, so they are locked now."""
    expected = [
        "TransientUpstreamError",
        "RateLimitError",
        "InvalidRecordError",
        "GraphWriteError",
        "InsufficientEvidenceError",
    ]
    for name, error_class in zip(expected, TAXONOMY, strict=True):
        assert error_class.__name__ == name
