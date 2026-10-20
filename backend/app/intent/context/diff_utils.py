"""Unified-diff helpers for prompt context.

Only hunks are quoted, never the whole patch, and every hunk keeps its
``@@`` header so the model can name a changed line honestly. No hunk is
mapped to a symbol here; target linking is validated against the graph.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@")

DEFAULT_MAX_HUNKS = 6
DEFAULT_MAX_LINES_PER_HUNK = 40


@dataclass(frozen=True)
class HunkText:
    header: str
    start_line: int
    end_line: int
    lines: tuple[str, ...]

    @property
    def text(self) -> str:
        return "\n".join((self.header, *self.lines))


def split_hunks(patch: str | None) -> list[HunkText]:
    if not patch:
        return []
    hunks: list[HunkText] = []
    current_header: str | None = None
    current_lines: list[str] = []
    current_start = 0
    current_end = 0
    for line in patch.splitlines():
        if _HUNK_HEADER.match(line):
            if current_header is not None:
                hunks.append(
                    HunkText(
                        header=current_header,
                        start_line=current_start,
                        end_line=current_end,
                        lines=tuple(current_lines),
                    )
                )
            current_header = line
            current_start, current_end = _parse_header_span(line)
            current_lines = []
            continue
        if current_header is not None:
            current_lines.append(line)
    if current_header is not None:
        hunks.append(
            HunkText(
                header=current_header,
                start_line=current_start,
                end_line=current_end,
                lines=tuple(current_lines),
            )
        )
    return hunks


def select_hunks(
    patch: str | None,
    *,
    max_hunks: int = DEFAULT_MAX_HUNKS,
    max_lines_per_hunk: int = DEFAULT_MAX_LINES_PER_HUNK,
    around_line: int | None = None,
) -> list[HunkText]:
    """Return at most ``max_hunks`` hunks, optionally prioritising one line."""
    hunks = split_hunks(patch)
    if not hunks:
        return []
    if around_line is not None:
        containing = [
            hunk
            for hunk in hunks
            if hunk.start_line <= around_line <= max(hunk.end_line, hunk.start_line)
        ]
        if containing:
            hunks = containing + [h for h in hunks if h not in containing]
    selected = []
    for hunk in hunks[: max(0, max_hunks)]:
        lines = hunk.lines
        if len(lines) > max_lines_per_hunk:
            lines = (
                *lines[:max_lines_per_hunk],
                "[... hunk truncated ...]",
            )
        selected.append(
            HunkText(
                header=hunk.header,
                start_line=hunk.start_line,
                end_line=hunk.end_line,
                lines=tuple(lines),
            )
        )
    return selected


def format_patch(
    patch: str | None,
    *,
    max_hunks: int = DEFAULT_MAX_HUNKS,
    max_lines_per_hunk: int = DEFAULT_MAX_LINES_PER_HUNK,
    around_line: int | None = None,
) -> str:
    hunks = select_hunks(
        patch,
        max_hunks=max_hunks,
        max_lines_per_hunk=max_lines_per_hunk,
        around_line=around_line,
    )
    return "\n".join(hunk.text for hunk in hunks)


def _parse_header_span(header: str) -> tuple[int, int]:
    match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", header)
    if match is None:
        return 0, 0
    start = int(match.group(1))
    count = int(match.group(2) or 1)
    return start, max(start, start + count - 1)


__all__ = [
    "DEFAULT_MAX_HUNKS",
    "DEFAULT_MAX_LINES_PER_HUNK",
    "HunkText",
    "format_patch",
    "select_hunks",
    "split_hunks",
]
