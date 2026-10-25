"""Hybrid retrieval and answer synthesis (Component 5).

Phases 1–8 own question understanding, index definitions, the four retrieval
strategies, WRRF rank fusion, explainable reranking, the compact evidence pack,
constrained answer synthesis, claim-level citation validation and deterministic
refusal behavior. Nothing here talks to Neo4j except the read-only adapters,
and nothing here generates Cypher from user text.
"""

from __future__ import annotations

__all__: list[str] = []
