"""Shared helpers for intent edge records."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol


class EdgeLike(Protocol):
    """The identity fields shared by validation and evaluation edge records."""

    @property
    def rel_type(self) -> str: ...

    @property
    def start_id(self) -> str: ...

    @property
    def end_id(self) -> str: ...


def dedupe_edges[EdgeT: EdgeLike](edges: Iterable[EdgeT]) -> list[EdgeT]:
    """Drop repeated relationship/endpoint triples while preserving order."""

    seen: set[tuple[str, str, str]] = set()
    unique: list[EdgeT] = []
    for edge in edges:
        key = (edge.rel_type, edge.start_id, edge.end_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(edge)
    return unique


__all__ = ["dedupe_edges"]
