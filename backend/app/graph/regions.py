"""Unified-diff hunk parsing for CodeRegion nodes.

GitHub sends one patch per changed file. Each ``@@`` header gives the
new-file span a hunk touches. We use that span as the region and never try
to infer which symbol a hunk belongs to: that mapping needs the parsed file
and stays in Component 3.

A hunk with no new-side lines (a pure deletion) is anchored at the line
where the deletion happened. An empty new file (``+0,0``) has no new-side
line to anchor to, so it produces no region.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


@dataclass(frozen=True)
class HunkSpan:
    """New-file line span for one unified-diff hunk."""

    start_line: int
    end_line: int
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str


def parse_patch_hunks(patch: str | None) -> list[HunkSpan]:
    """Return one span per hunk in ``patch``, in patch order.

    Counts omitted from the header default to 1, matching unified-diff
    syntax. Malformed headers are ignored rather than guessed.
    """
    if not patch:
        return []

    spans: list[HunkSpan] = []
    for line in patch.splitlines():
        match = _HUNK_HEADER.match(line)
        if match is None:
            continue
        old_start = int(match.group("old_start"))
        old_count = int(match.group("old_count") or 1)
        new_start = int(match.group("new_start"))
        new_count = int(match.group("new_count") or 1)
        if new_start < 1:
            # A deleted file has no new-side line to anchor to.
            continue
        end_line = max(new_start, new_start + new_count - 1)
        spans.append(
            HunkSpan(
                start_line=new_start,
                end_line=end_line,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                header=line,
            )
        )
    return spans


__all__ = ["HunkSpan", "parse_patch_hunks"]
