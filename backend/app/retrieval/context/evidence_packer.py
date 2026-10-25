"""Deterministic evidence packing.

The answer model receives this text and nothing else. Evidence aliases are
assigned here, so the citation validator can resolve ``[E3]`` to exactly one
retrieved evidence ID and reject everything else.

Priority follows the product contract: the decision claim and status come
first, then the changed hunk, review comments anchored to the changed lines,
commit messages, linked issue context, the superseding decision and the
author/date metadata attached to each item. Under a token budget the lowest
priority evidence goes first.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.retrieval.config import RetrievalConfig
from app.retrieval.context.token_budget import TokenBudget, estimate_tokens
from app.retrieval.models import (
    DecisionCandidate,
    DecisionState,
    EvidenceItem,
    GraphContext,
    QuestionUnderstanding,
    RankedCandidate,
    SupersessionRecord,
)

_KIND_PRIORITY = {
    "changed_file": 0,
    "diff": 0,
    "review_comment": 1,
    "commit": 2,
    "issue_comment": 3,
    "issue": 4,
    "pull_request": 5,
    "review": 6,
    "doc_file": 7,
    "doc": 7,
    "person": 8,
}

_KIND_LABELS = {
    "changed_file": "Changed diff hunk",
    "diff": "Changed diff hunk",
    "review_comment": "Review comment",
    "review": "Review",
    "commit": "Commit message",
    "issue_comment": "Issue comment",
    "issue": "Issue",
    "pull_request": "Pull request",
    "doc_file": "Documentation",
    "doc": "Documentation",
    "person": "Participant",
}

_ISO_DATE = re.compile(r"^(?P<date>\d{4}-\d{2}-\d{2})")
_PULL_REQUEST_KEY = re.compile(r"(?:^|:)pr:(?P<number>\d+)$")

_RULES = (
    "- Use only evidence IDs provided.",
    "- Cite evidence inline as [E1], [E2].",
    "- If evidence does not support an answer, return INSUFFICIENT_EVIDENCE.",
    "- Do not use outside knowledge about the repository.",
    "- Do not speculate about maintainer intent.",
    "- Keep the answer under 180 words.",
)


class EvidencePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    question: str
    decisions: list[DecisionCandidate] = Field(default_factory=list)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    alias_map: dict[str, str] = Field(default_factory=dict)
    decision_alias_map: dict[str, str] = Field(default_factory=dict)
    graph_path: list[str] = Field(default_factory=list)
    supersession: list[SupersessionRecord] = Field(default_factory=list)
    omitted_evidence_ids: list[str] = Field(default_factory=list)
    token_count: int = 0

    def resolve_citation(self, token: str) -> str | None:
        cleaned = token.strip().strip("[]").strip()
        if not cleaned:
            return None
        upper = cleaned.upper()
        if upper in self.alias_map:
            return self.alias_map[upper]
        if cleaned in set(self.alias_map.values()):
            return cleaned
        return None

    def evidence_ids(self) -> list[str]:
        return [item.evidence_id for item in self.evidence]

    def alias_for(self, evidence_id: str) -> str | None:
        for alias, resolved in self.alias_map.items():
            if resolved == evidence_id:
                return alias
        return None


class EvidencePacker:
    def __init__(self, config: RetrievalConfig | None = None) -> None:
        self.config = config or RetrievalConfig()

    def pack(
        self,
        *,
        understanding: QuestionUnderstanding,
        ranked: Sequence[RankedCandidate],
        contexts: Mapping[str, GraphContext],
    ) -> EvidencePack:
        cfg = self.config
        decisions = [item.candidate for item in ranked[: cfg.max_decisions]]
        decision_aliases = {
            f"D{index}": decision.decision_id
            for index, decision in enumerate(decisions, start=1)
        }
        available: list[EvidenceItem] = []
        for decision in decisions:
            context = contexts.get(decision.decision_id)
            if context is None:
                continue
            for item in self._ordered(context.evidence):
                if item.evidence_id not in {row.evidence_id for row in available}:
                    available.append(item)
        selected = self._select_round_robin(
            decisions, contexts, limit=cfg.max_evidence_items
        )
        supersession = _supersession(decisions, contexts)
        graph_path = _graph_path(decisions, contexts, selected)

        text = self._assemble(
            understanding=understanding,
            decisions=decisions,
            decision_aliases=decision_aliases,
            evidence=selected,
            graph_path=graph_path,
            supersession=supersession,
        )
        omitted = [
            item.evidence_id
            for item in available
            if item.evidence_id not in {row.evidence_id for row in selected}
        ]
        if estimate_tokens(text) > cfg.max_context_tokens:
            while selected and estimate_tokens(text) > cfg.max_context_tokens:
                removed = selected.pop()
                omitted.append(removed.evidence_id)
                graph_path = _graph_path(decisions, contexts, selected)
                text = self._assemble(
                    understanding=understanding,
                    decisions=decisions,
                    decision_aliases=decision_aliases,
                    evidence=selected,
                    graph_path=graph_path,
                    supersession=supersession,
                )
            if estimate_tokens(text) > cfg.max_context_tokens:
                text = self._assemble(
                    understanding=understanding,
                    decisions=decisions,
                    decision_aliases=decision_aliases,
                    evidence=[],
                    graph_path=[],
                    supersession=supersession,
                    include_graph_path=False,
                )
            if estimate_tokens(text) > cfg.max_context_tokens:
                text = _truncate_preserving_rules(text, cfg.max_context_tokens)

        aliases = {
            f"E{index}": item.evidence_id
            for index, item in enumerate(selected, start=1)
        }
        return EvidencePack(
            text=text,
            question=understanding.question,
            decisions=decisions,
            evidence=selected,
            alias_map=aliases,
            decision_alias_map=decision_aliases,
            graph_path=_graph_path(decisions, contexts, selected),
            supersession=supersession,
            omitted_evidence_ids=list(dict.fromkeys(omitted)),
            token_count=estimate_tokens(text),
        )

    def _ordered(self, evidence: Sequence[EvidenceItem]) -> list[EvidenceItem]:
        return sorted(
            evidence,
            key=lambda item: (
                _KIND_PRIORITY.get(item.kind, 20),
                0 if item.path and item.start_line is not None else 1,
                item.evidence_id,
            ),
        )

    def _select_round_robin(
        self,
        decisions: Sequence[DecisionCandidate],
        contexts: Mapping[str, GraphContext],
        *,
        limit: int,
    ) -> list[EvidenceItem]:
        queues: list[list[EvidenceItem]] = []
        for decision in decisions:
            context = contexts.get(decision.decision_id)
            rows = [] if context is None else self._ordered(context.evidence)
            queues.append(list(rows))
        selected: list[EvidenceItem] = []
        seen: set[str] = set()
        while len(selected) < limit:
            progressed = False
            for queue in queues:
                if not queue:
                    continue
                progressed = True
                item = queue.pop(0)
                if item.evidence_id in seen:
                    continue
                seen.add(item.evidence_id)
                selected.append(item)
                if len(selected) >= limit:
                    break
            if not progressed:
                break
        return selected

    def _assemble(
        self,
        *,
        understanding: QuestionUnderstanding,
        decisions: Sequence[DecisionCandidate],
        decision_aliases: Mapping[str, str],
        evidence: Sequence[EvidenceItem],
        graph_path: Sequence[str],
        supersession: Sequence[SupersessionRecord],
        include_graph_path: bool = True,
    ) -> str:
        lines = ["QUESTION:", understanding.question, "", "CANDIDATE DECISION:"]
        alias_by_id = {
            decision_id: alias for alias, decision_id in decision_aliases.items()
        }
        for decision in decisions:
            alias = alias_by_id.get(decision.decision_id, decision.decision_id)
            claim = decision.claim or "(no claim recorded)"
            lines.append(f"[{alias}] {claim}")
            lines.append(f"Status: {decision.status.value}")
            lines.append(f"Confidence: {decision.confidence.value}")
            lines.append(f"Targets: {_targets_text(decision.target_ids)}")
            lines.append("")

        lines.append("EVIDENCE:")
        if not evidence:
            lines.append("None.")
        for index, item in enumerate(evidence, start=1):
            header = f"[E{index}] {_kind_label(item.kind)}"
            if item.author:
                header += f" by {item.author}"
            date = _display_date(item.created_at)
            if date:
                header += f" on {date}"
            lines.append(header)
            location = _location(item)
            if location:
                lines.append(location)
            excerpt = " ".join(item.excerpt.split())
            if excerpt:
                limit = self.config.max_excerpt_chars
                if len(excerpt) > limit:
                    excerpt = excerpt[:limit].rstrip() + " …"
                lines.append(f'"{excerpt}"')
            lines.append("")

        if include_graph_path:
            lines.append("GRAPH PATH:")
            rendered_path = _render_graph_path(
                graph_path, decision_aliases, evidence
            )
            if rendered_path:
                for index, part in enumerate(rendered_path):
                    lines.append(part if index == 0 else f"→ {part}")
            else:
                lines.append("None found.")
            lines.append("")

        lines.append("SUPERSESSION:")
        if supersession:
            for record in supersession:
                lines.append(
                    f"{record.decision_id} --{record.relation}--> "
                    f"{record.superseded_by}"
                )
        else:
            lines.append("None found in ingested window.")
        lines.append("")

        lines.append("ANSWER RULES:")
        lines.extend(_RULES)
        return "\n".join(lines)


def _kind_label(kind: str) -> str:
    known = _KIND_LABELS.get(kind)
    if known is not None:
        return known
    cleaned = kind.replace("-", "_").replace("_", " ").strip()
    return cleaned.title() if cleaned else "Evidence"


def _display_date(value: str | None) -> str | None:
    if not value:
        return None
    match = _ISO_DATE.match(value.strip())
    if match is not None:
        return match.group("date")
    stripped = value.strip()
    return stripped or None


def _targets_text(targets: Sequence[str]) -> str:
    values = []
    for target in targets:
        _, separator, tail = target.strip().partition(":")
        values.append(tail.strip() if separator and tail.strip() else target.strip())
    unique = [value for value in dict.fromkeys(values) if value]
    return ", ".join(unique) if unique else "unknown"


def _location(item: EvidenceItem) -> str | None:
    parts: list[str] = []
    if item.commit_sha:
        parts.append(f"SHA {item.commit_sha}")
    if item.issue_number is not None:
        parts.append(f"Issue #{item.issue_number}")
    if item.pr_number is not None:
        parts.append(f"PR #{item.pr_number}")
    if item.path:
        if item.start_line is not None and item.end_line is not None:
            parts.append(f"{item.path}:{item.start_line}-{item.end_line}")
        elif item.start_line is not None:
            parts.append(f"{item.path}:{item.start_line}")
        else:
            parts.append(item.path)
    return ", ".join(parts) if parts else item.url


def _render_graph_path(
    parts: Sequence[str],
    decision_aliases: Mapping[str, str],
    evidence: Sequence[EvidenceItem],
) -> list[str]:
    alias_by_decision = {
        decision_id: alias for alias, decision_id in decision_aliases.items()
    }
    evidence_alias_by_id = {
        item.evidence_id: f"E{index}"
        for index, item in enumerate(evidence, start=1)
    }
    rendered: list[str] = []
    seen_evidence: set[str] = set()
    for part in parts:
        if part.startswith("Decision:"):
            decision_id = part.split(":", 1)[1]
            alias = alias_by_decision.get(decision_id)
            rendered.append(f"Decision:{alias}" if alias else part)
            continue
        if part.startswith("Evidence:"):
            evidence_id = part.split(":", 1)[1]
            alias = evidence_alias_by_id.get(evidence_id)
            if alias is not None:
                seen_evidence.add(alias)
            continue
        rendered.append(_format_pull_request(part))
    if seen_evidence:
        ordered = sorted(seen_evidence, key=lambda alias: int(alias[1:]))
        rendered.append("Evidence:" + ",".join(ordered))
    return rendered


def _truncate_preserving_rules(text: str, max_tokens: int) -> str:
    marker = "ANSWER RULES:"
    index = text.find(marker)
    if index < 0:
        return TokenBudget(max_tokens).truncate(text)
    rules = text[index:]
    rules_tokens = estimate_tokens(rules)
    remaining = max_tokens - rules_tokens - 1
    if remaining < 1:
        return TokenBudget(max_tokens).truncate(rules)
    head = TokenBudget(remaining).truncate(text[:index]).rstrip()
    return f"{head}\n{rules}" if head else rules


def _format_pull_request(value: str) -> str:
    raw = value.split(":", 1)[1] if value.startswith("PullRequest:") else value
    if raw.startswith("#") and value.startswith("PullRequest:"):
        return value
    match = _PULL_REQUEST_KEY.search(raw)
    if match is not None:
        return f"PullRequest:#{match.group('number')}"
    return value


def _graph_path(
    decisions: Sequence[DecisionCandidate],
    contexts: Mapping[str, GraphContext],
    evidence: Sequence[EvidenceItem],
) -> list[str]:
    selected_ids = {item.evidence_id for item in evidence}
    path: list[str] = []
    for decision in decisions:
        context = contexts.get(decision.decision_id)
        if context is None:
            continue
        for part in context.path():
            if part.startswith("Evidence:") and part.split(":", 1)[1] not in selected_ids:
                continue
            path.append(part)
    return list(dict.fromkeys(path))


def _supersession(
    decisions: Sequence[DecisionCandidate],
    contexts: Mapping[str, GraphContext],
) -> list[SupersessionRecord]:
    records: list[SupersessionRecord] = []
    seen: set[tuple[str, str]] = set()
    for decision in decisions:
        context = contexts.get(decision.decision_id)
        later = list(context.later_decisions) if context else []
        if decision.superseded_by:
            later.append(decision.superseded_by)
        for next_id in later:
            key = (decision.decision_id, next_id)
            if key in seen:
                continue
            seen.add(key)
            records.append(
                SupersessionRecord(
                    decision_id=decision.decision_id,
                    superseded_by=next_id,
                    relation=(
                        "REVERSED_BY"
                        if decision.status is DecisionState.REVERSED
                        else "SUPERSEDED_BY"
                    ),
                )
            )
    return records


__all__ = ["EvidencePack", "EvidencePacker"]
