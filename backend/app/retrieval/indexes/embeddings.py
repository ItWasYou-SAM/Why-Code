"""Embedding boundary and a deterministic offline implementation.

The deterministic provider hashes tokens into a fixed-size vector. It is not
semantically useful; it exists so the pipeline can be exercised without a
paid embedding call and so reruns are byte-identical.
"""

from __future__ import annotations

import hashlib
import math
import re

from app.core.errors import InvalidRecordError
from app.graph.records import GraphNode, GraphRecords
from app.retrieval.indexes.index_manager import (
    VECTOR_TARGET_BY_LABEL,
    VectorTarget,
)
from app.retrieval.ports import EmbeddingProvider

_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]*")
DEFAULT_EMBEDDING_TEXT_CHARS = 8000


class HashingEmbeddingProvider:
    def __init__(self, dimensions: int = 1536) -> None:
        if dimensions < 1:
            raise ValueError("dimensions must be positive")
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = _TOKEN.findall(text.lower())
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:8], "big") % self.dimensions
            sign = 1.0 if digest[8] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm:
            vector = [value / norm for value in vector]
        return vector


def embedding_text(
    properties: dict[str, object], target: VectorTarget
) -> str:
    """Join the configured source fields in a stable order.

    The fields are the contract from the phase plan: a decision embeds its
    claim and rationale, a symbol its qualified name and docstring, and so on.
    Missing or blank fields are skipped rather than encoded as the string
    ``"None"``.
    """
    parts: list[str] = []
    for name in target.properties:
        value = properties.get(name)
        if value is None:
            continue
        text = " ".join(str(value).split()).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def embed_graph_records(
    records: GraphRecords,
    *,
    embedder: EmbeddingProvider,
    dimensions: int | None = None,
    max_text_chars: int = DEFAULT_EMBEDDING_TEXT_CHARS,
) -> GraphRecords:
    """Attach deterministic embeddings to every vector-indexed node.

    Embeddings are computed in Python before the batch write, so a failed
    embedding call fails the load before any node is merged. Nodes whose
    target text is empty keep no embedding; those nodes stay out of the
    vector index rather than being represented by a zero vector.
    """
    if max_text_chars < 1:
        raise ValueError("max_text_chars must be positive")
    nodes: list[GraphNode] = []
    for node in records.nodes:
        target = VECTOR_TARGET_BY_LABEL.get(node.label)
        properties = dict(node.properties)
        properties.pop("embedding", None)
        if target is not None:
            text = embedding_text(node.properties, target)
            if text:
                vector = embedder.embed(text[:max_text_chars])
                _validate_vector(vector, dimensions=dimensions)
                properties["embedding"] = vector
        nodes.append(node.model_copy(update={"properties": properties}))
    return records.model_copy(update={"nodes": nodes})


def _validate_vector(vector: list[float], *, dimensions: int | None) -> None:
    if not vector:
        raise InvalidRecordError(
            "embedding provider returned an empty vector",
            component="retrieval",
        )
    if not all(
        isinstance(value, (int, float)) and math.isfinite(value)
        for value in vector
    ):
        raise InvalidRecordError(
            "embedding provider returned a non-finite vector",
            component="retrieval",
        )
    if dimensions is not None and len(vector) != dimensions:
        raise InvalidRecordError(
            "embedding dimensions do not match the vector index "
            f"({len(vector)} != {dimensions})",
            component="retrieval",
        )


__all__ = [
    "DEFAULT_EMBEDDING_TEXT_CHARS",
    "EmbeddingProvider",
    "HashingEmbeddingProvider",
    "embed_graph_records",
    "embedding_text",
]
