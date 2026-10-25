"""In-memory graph backend for the demo.

The adapter implements exactly the same protocols as the Neo4j backend. It
uses only static data and deterministic arithmetic; no Cypher, network call or
clock is involved. Full-text scoring is token-overlap over the decision claim
and rationale, which is the same text ``decision_fulltext`` indexes.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.retrieval.demo.embeddings import DemoEmbeddingProvider, cosine
from app.retrieval.models import DecisionCandidate, EvidenceItem, GraphContext
from app.retrieval.question.normalization import content_tokens, token_forms


class DemoBackend:
    def __init__(
        self,
        *,
        contexts: dict[str, GraphContext],
        embedder: DemoEmbeddingProvider,
    ) -> None:
        self.contexts = dict(contexts)
        self.embedder = embedder
        self.decisions = {
            decision_id: context.decision
            for decision_id, context in self.contexts.items()
        }
        self.evidence_by_id = {
            item.evidence_id: item
            for context in self.contexts.values()
            for item in context.evidence
        }
        self._search_text = {
            decision_id: _decision_text(context)
            for decision_id, context in self.contexts.items()
        }
        self._vectors = {
            decision_id: embedder.embed(text)
            for decision_id, text in self._search_text.items()
        }
        self._exact_index = self._build_exact_index()
        self._history_index = self._build_history_index()

    # GraphBackend -----------------------------------------------------

    def exact_search(
        self, *, repo: str, identifiers: Sequence[str], limit: int
    ) -> Sequence[DecisionCandidate]:
        del repo
        selected: list[str] = []
        for identifier in identifiers:
            for decision_id in self._exact_index.get(identifier.strip(), []):
                if decision_id not in selected:
                    selected.append(decision_id)
        return [self.decisions[decision_id] for decision_id in selected[:limit]]

    def fulltext_search(
        self, *, repo: str, query: str, limit: int
    ) -> Sequence[DecisionCandidate]:
        del repo
        forms = _query_forms(query)
        if not forms:
            return []
        scored: list[tuple[float, str]] = []
        for decision_id, text in self._search_text.items():
            score = len(forms & _query_forms(text)) / len(forms)
            if score > 0:
                scored.append((score, decision_id))
        if not scored:
            return []
        best = max(score for score, _ in scored)
        threshold = max(0.25, best * 0.60)
        scored = [row for row in scored if row[0] >= threshold]
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [self.decisions[decision_id] for _, decision_id in scored[:limit]]

    def vector_search(
        self, *, repo: str, embedding: Sequence[float], limit: int
    ) -> Sequence[DecisionCandidate]:
        del repo
        query = list(embedding)
        scored = [
            (cosine(query, vector), decision_id)
            for decision_id, vector in self._vectors.items()
        ]
        scored = [row for row in scored if row[0] > 0]
        if not scored:
            return []
        best = max(score for score, _ in scored)
        threshold = max(0.05, best * 0.70)
        scored = [row for row in scored if row[0] >= threshold]
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [self.decisions[decision_id] for _, decision_id in scored[:limit]]

    def expand(self, *, repo: str, decision_id: str) -> GraphContext | None:
        del repo
        return self.contexts.get(decision_id)

    # LookupBackend ----------------------------------------------------

    def get_decision(
        self, *, repo: str, decision_id: str
    ) -> GraphContext | None:
        del repo
        return self.contexts.get(decision_id)

    def get_evidence(
        self, *, repo: str, evidence_id: str
    ) -> EvidenceItem | None:
        del repo
        return self.evidence_by_id.get(evidence_id)

    def entity_history(
        self, *, repo: str, entity_id: str
    ) -> Sequence[GraphContext]:
        del repo
        decision_ids = self._history_index.get(entity_id.strip(), [])
        return [self.contexts[decision_id] for decision_id in decision_ids]

    # Indexes ----------------------------------------------------------

    def _build_exact_index(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}

        def add(identifier: str, decision_id: str) -> None:
            cleaned = identifier.strip()
            if not cleaned:
                return
            bucket = index.setdefault(cleaned, [])
            if decision_id not in bucket:
                bucket.append(decision_id)

        for context in self.contexts.values():
            decision_id = context.decision.decision_id
            add(decision_id, decision_id)
            for target in context.targets:
                _, separator, tail = target.partition(":")
                if separator and tail.strip():
                    add(tail, decision_id)
            for pull_request in context.pull_requests:
                _, separator, tail = pull_request.partition(":")
                if separator and tail.strip():
                    add(tail, decision_id)
            for item in context.evidence:
                add(item.evidence_id, decision_id)
        return index

    def _build_history_index(self) -> dict[str, list[str]]:
        index: dict[str, list[str]] = {}
        for context in self.contexts.values():
            decision_id = context.decision.decision_id
            identifiers = {decision_id}
            for target in context.targets:
                _, separator, tail = target.partition(":")
                if separator and tail.strip():
                    identifiers.add(tail.strip())
            for identifier in identifiers:
                index.setdefault(identifier, []).append(decision_id)
        return index


def _decision_text(context: GraphContext) -> str:
    return " ".join(
        part
        for part in (
            context.decision.claim,
            context.decision.rationale,
        )
        if part.strip()
    )


def _query_forms(text: str) -> set[str]:
    forms: set[str] = set()
    for token in content_tokens(text):
        forms.update(token_forms(token))
    return forms


__all__ = ["DemoBackend"]
