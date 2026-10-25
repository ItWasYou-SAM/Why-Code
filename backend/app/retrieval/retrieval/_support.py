"""Small ranking helpers shared by the source retrievers."""

from __future__ import annotations

from collections.abc import Sequence

from app.retrieval.models import DecisionCandidate, RetrievalSource


def tag_source(
    candidates: Sequence[DecisionCandidate],
    source: RetrievalSource,
    *,
    limit: int,
    start_rank: int = 1,
) -> list[DecisionCandidate]:
    """Deduplicate a source's candidates and stamp their ranks.

    Rank, not score, is the only thing fusion sees. If a backend returns the
    same decision twice (for example through two matching identifiers), the
    first occurrence wins because it had the stronger rank.
    """
    tagged: list[DecisionCandidate] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.decision_id in seen:
            continue
        seen.add(candidate.decision_id)
        rank = start_rank + len(tagged)
        sources = dict(candidate.sources)
        sources[source] = rank
        tagged.append(candidate.model_copy(update={"sources": sources}))
        if len(tagged) >= limit:
            break
    return tagged
