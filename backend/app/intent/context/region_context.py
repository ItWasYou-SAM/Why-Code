"""Region-centred excerpts built from unified-diff hunks.

The packer never reads a whole file. It reconstructs two narrow views from
the patch GitHub already gave us:

* the **current** side (context plus added lines, numbered on the new file);
* the **previous** side (context plus removed lines, numbered on the old file).

The same hunk is also kept verbatim so the model can see the change in diff
form. A hunk is selected because it contains an anchored review comment or
because it belongs to a changed file the candidate already points at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.intent.context.diff_utils import HunkText, split_hunks
from app.intent.selection.evidence_selector import EvidenceItem

_HUNK_OLD = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_CONTEXT_WINDOW = 24


@dataclass(frozen=True)
class RegionContext:
    path: str
    header: str
    start_line: int
    end_line: int
    old_start: int
    old_end: int
    current_code: str
    previous_code: str
    diff_text: str
    comment_ids: tuple[str, ...] = ()

    def contains_line(self, line: int, *, side: str = "RIGHT") -> bool:
        if side.upper() == "LEFT":
            return self.old_start <= line <= self.old_end
        return self.start_line <= line <= self.end_line


@dataclass(frozen=True)
class _Anchor:
    line: int
    side: str
    comment_id: str


def build_region_contexts(
    files: list[EvidenceItem],
    review_comments: list[EvidenceItem],
    *,
    max_regions: int = 4,
    max_lines_per_hunk: int = 40,
    max_code_lines: int = _CONTEXT_WINDOW,
) -> list[RegionContext]:
    """Return at most ``max_regions`` hunks, anchored hunks first."""
    anchors = _anchors_by_path(review_comments)
    regions: list[RegionContext] = []
    for file in files:
        if len(regions) >= max_regions:
            break
        payload = file.payload
        path = payload.get("path")
        patch = payload.get("patch")
        if not isinstance(path, str) or not path or not isinstance(patch, str):
            continue
        hunks = split_hunks(patch)
        if not hunks:
            continue
        file_anchors = anchors.get(path, [])
        for hunk in _ordered_hunks(hunks, file_anchors):
            if len(regions) >= max_regions:
                break
            regions.append(
                _region_from_hunk(
                    path=path,
                    hunk=hunk,
                    anchors=[
                        anchor
                        for anchor in file_anchors
                        if _hunk_contains(hunk, anchor)
                    ],
                    max_lines_per_hunk=max_lines_per_hunk,
                    max_code_lines=max_code_lines,
                )
            )
    return regions


def _anchors_by_path(
    review_comments: list[EvidenceItem],
) -> dict[str, list[_Anchor]]:
    anchors: dict[str, list[_Anchor]] = {}
    for comment in review_comments:
        payload = comment.payload
        path = payload.get("path")
        if not isinstance(path, str) or not path:
            continue
        line = payload.get("line")
        start = payload.get("start_line")
        lines: list[int] = []
        if isinstance(line, int):
            lines.append(line)
        if isinstance(start, int) and start not in lines:
            lines.append(start)
        if not lines:
            continue
        side = str(payload.get("side") or "RIGHT").upper()
        for value in lines:
            anchors.setdefault(path, []).append(
                _Anchor(line=value, side=side, comment_id=comment.record_id)
            )
    for values in anchors.values():
        values.sort(key=lambda anchor: (anchor.line, anchor.comment_id))
    return anchors


def _ordered_hunks(
    hunks: list[HunkText], anchors: list[_Anchor]
) -> list[HunkText]:
    if not anchors:
        return hunks
    matching = [hunk for hunk in hunks if _hunk_matches(hunk, anchors)]
    if not matching:
        return hunks
    return matching + [hunk for hunk in hunks if hunk not in matching]


def _hunk_matches(hunk: HunkText, anchors: list[_Anchor]) -> bool:
    return any(_hunk_contains(hunk, anchor) for anchor in anchors)


def _hunk_contains(hunk: HunkText, anchor: _Anchor) -> bool:
    old_start, old_end = _old_span(hunk.header)
    if anchor.side == "LEFT":
        return old_start <= anchor.line <= old_end
    return hunk.start_line <= anchor.line <= hunk.end_line


def _old_span(header: str) -> tuple[int, int]:
    match = _HUNK_OLD.match(header)
    if match is None:
        return 0, 0
    start = int(match.group(1))
    count = int(match.group(2) or 1)
    return start, max(start, start + count - 1)


def _region_from_hunk(
    *,
    path: str,
    hunk: HunkText,
    anchors: list[_Anchor],
    max_lines_per_hunk: int,
    max_code_lines: int,
) -> RegionContext:
    old_start, old_end = _old_span(hunk.header)
    current, previous = _split_sides(
        hunk, old_start=old_start, new_start=hunk.start_line
    )
    diff_lines = hunk.lines
    if len(diff_lines) > max_lines_per_hunk:
        diff_lines = (
            *diff_lines[:max_lines_per_hunk],
            "[... hunk truncated ...]",
        )
    return RegionContext(
        path=path,
        header=hunk.header,
        start_line=hunk.start_line,
        end_line=hunk.end_line,
        old_start=old_start,
        old_end=old_end,
        current_code=_render_side(current, max_code_lines),
        previous_code=_render_side(previous, max_code_lines),
        diff_text="\n".join((hunk.header, *diff_lines)),
        comment_ids=tuple(sorted({anchor.comment_id for anchor in anchors})),
    )


def _split_sides(
    hunk: HunkText, *, old_start: int, new_start: int
) -> tuple[list[tuple[int, str, bool]], list[tuple[int, str, bool]]]:
    current: list[tuple[int, str, bool]] = []
    previous: list[tuple[int, str, bool]] = []
    old_line = old_start
    new_line = new_start
    for raw in hunk.lines:
        if not raw or raw.startswith("\\"):
            continue
        prefix = raw[0]
        text = raw[1:]
        if prefix == "-":
            previous.append((old_line, text, True))
            old_line += 1
        elif prefix == "+":
            current.append((new_line, text, True))
            new_line += 1
        else:
            previous.append((old_line, text, False))
            current.append((new_line, text, False))
            old_line += 1
            new_line += 1
    return current, previous


def _render_side(
    entries: list[tuple[int, str, bool]], max_lines: int
) -> str:
    if not entries:
        return ""
    window = _window(entries, max_lines)
    return "\n".join(
        text if line_no < 0 else f"{line_no:>5} | {text}"
        for line_no, text, _ in window
    )


def _window(
    entries: list[tuple[int, str, bool]], max_lines: int
) -> list[tuple[int, str, bool]]:
    if max_lines < 1 or len(entries) <= max_lines:
        return entries
    first_change = next(
        (index for index, entry in enumerate(entries) if entry[2]), 0
    )
    start = max(0, first_change - max_lines // 2)
    end = min(len(entries), start + max_lines)
    start = max(0, end - max_lines)
    clipped = entries[start:end]
    omitted = len(entries) - len(clipped)
    if omitted <= 0:
        return entries
    marker = (-1, f"[... {omitted} nearby lines omitted ...]", False)
    return [*clipped, marker]


__all__ = ["RegionContext", "build_region_contexts"]
