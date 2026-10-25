"""Deterministic vocabulary embeddings for the offline demo.

Unlike the hashing provider, this one puts related words on the same axis
(``retries``/``retry``, ``timeouts``/``timeout``, ``limited``/``max``) so the
vector source is useful without a paid embedding call. It is still a fixture:
the vocabulary is fixed and the output is stable across runs.
"""

from __future__ import annotations

import math

from app.retrieval.question.normalization import content_tokens, token_forms

_VOCABULARY = (
    "retry",
    "default",
    "disabled",
    "enabled",
    "connection",
    "failure",
    "simple",
    "max",
    "attempt",
    "idempotent",
    "duplicate",
    "request",
    "timeout",
    "connect",
    "read",
    "write",
    "pool",
    "client",
    "phase",
    "server",
    "transient",
    "revision",
    "revised",
    "reverse",
    "reversed",
    "approach",
    "automatic",
    "network",
    "error",
    "explicit",
    "zero",
    "opt",
    "current",
    "historical",
    "guard",
    "transport",
    "configuration",
    "starvation",
    "outage",
)

_INDEX: dict[str, int] = {}
for _position, _word in enumerate(_VOCABULARY):
    for _form in token_forms(_word):
        _INDEX.setdefault(_form, _position)


class DemoEmbeddingProvider:
    def __init__(self, dimensions: int | None = None) -> None:
        self.dimensions = dimensions or len(_VOCABULARY)
        if self.dimensions < len(_VOCABULARY):
            raise ValueError(
                "demo embedding dimensions must cover the fixed vocabulary"
            )

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in content_tokens(text):
            position = _INDEX.get(token)
            if position is None:
                # ``token_forms`` can map a plural or past tense; try each
                # form before giving up on the token.
                position = next(
                    (
                        _INDEX[form]
                        for form in token_forms(token)
                        if form in _INDEX
                    ),
                    None,
                )
            if position is not None:
                vector[position] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


__all__ = ["DemoEmbeddingProvider", "cosine"]
