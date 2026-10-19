"""Deterministic measurements of file content.

These are the only definitions of hash/size/line-count used across the
structural pass, so a content record, a File node and a validation check can
never disagree about the same bytes.
"""

from __future__ import annotations

import hashlib


def content_sha256(text: str) -> str:
    """SHA-256 of the UTF-8 encoding of ``text`` (stable, content-addressed)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def count_lines(text: str) -> int:
    """Number of lines, counting a trailing newline as a terminator."""
    return text.count("\n") + (0 if text.endswith("\n") else 1)


__all__ = ["content_sha256", "utf8_size", "count_lines"]
