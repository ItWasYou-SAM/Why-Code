"""Write-time embedding contracts.

The vector indexes are only useful if the nodes actually carry a vector. The
embedding step is deterministic, label-driven and fails before a write when
the provider returns the wrong shape.
"""

from __future__ import annotations

import pytest

from app.core.errors import InvalidRecordError
from app.graph.records import GraphNode, GraphRecords
from app.retrieval.indexes.embeddings import (
    HashingEmbeddingProvider,
    embed_graph_records,
    embedding_text,
)
from app.retrieval.indexes.index_manager import VECTOR_TARGET_BY_LABEL


def _records(*nodes: GraphNode) -> GraphRecords:
    return GraphRecords(nodes=list(nodes))


def _node(label: str, **properties: object) -> GraphNode:
    return GraphNode(label=label, key=f"key:{label}", properties=properties)


def test_embedding_text_uses_the_configured_fields_in_order() -> None:
    target = VECTOR_TARGET_BY_LABEL["Decision"]
    text = embedding_text(
        {
            "claim": "retries are limited",
            "rationale": "only connection errors should retry",
            "unrelated": "ignored",
        },
        target,
    )
    assert text == "retries are limited only connection errors should retry"


def test_blank_fields_are_skipped_not_stringified() -> None:
    target = VECTOR_TARGET_BY_LABEL["Symbol"]
    assert (
        embedding_text(
            {"qualified_name": "Client.send", "docstring": "  "},
            target,
        )
        == "Client.send"
    )


def test_every_embedded_label_receives_a_vector() -> None:
    nodes = [
        _node("Decision", claim="retry", rationale="transient failures"),
        _node("Evidence", excerpt="the PR body"),
        _node("Symbol", qualified_name="Client.send", docstring="Send."),
        _node("ConfigKey", key="retry.max_attempts"),
        _node("CodeRegion", excerpt="def send(): ..."),
    ]
    result = embed_graph_records(
        _records(*nodes),
        embedder=HashingEmbeddingProvider(dimensions=8),
        dimensions=8,
    )
    assert all("embedding" in node.properties for node in result.nodes)
    assert all(len(node.properties["embedding"]) == 8 for node in result.nodes)


def test_non_indexed_labels_do_not_get_a_vector() -> None:
    result = embed_graph_records(
        _records(_node("File", path="a.py")),
        embedder=HashingEmbeddingProvider(dimensions=8),
        dimensions=8,
    )
    assert "embedding" not in result.nodes[0].properties


def test_empty_text_has_no_vector_and_removes_a_stale_one() -> None:
    result = embed_graph_records(
        _records(
            _node(
                "Symbol",
                qualified_name="",
                docstring=None,
                embedding=[1.0, 0.0],
            )
        ),
        embedder=HashingEmbeddingProvider(dimensions=2),
        dimensions=2,
    )
    assert "embedding" not in result.nodes[0].properties


def test_embeddings_are_stable_across_runs() -> None:
    records = _records(
        _node("ConfigKey", key="retry.max_attempts"),
    )
    embedder = HashingEmbeddingProvider(dimensions=16)
    first = embed_graph_records(records, embedder=embedder, dimensions=16)
    second = embed_graph_records(records, embedder=embedder, dimensions=16)
    assert first == second


def test_wrong_dimension_fails_before_the_write() -> None:
    with pytest.raises(InvalidRecordError, match="dimensions"):
        embed_graph_records(
            _records(_node("ConfigKey", key="retry.max_attempts")),
            embedder=HashingEmbeddingProvider(dimensions=8),
            dimensions=16,
        )
