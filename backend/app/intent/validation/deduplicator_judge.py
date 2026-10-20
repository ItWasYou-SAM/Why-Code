"""One bounded LLM call to adjudicate a duplicate candidate (Phase 7).

Deterministic code finds the pair through target overlap and embedding
similarity. The model answers only whether the pair is the same decision, two
separate decisions, conflicting, or not decidable. It never chooses the
canonical node, unions evidence or writes an edge.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.intent.llm.schemas import DuplicateVerdict
from app.intent.llm.structured_client import StructuredClient
from app.intent.models import IntentBase
from app.intent.validation.deduplicator import DuplicateCandidate

SYSTEM = """\
You are a deterministic adjudicator, not an agent and not a chatbot.
You receive two records that deterministic code already identified as possible
duplicates because they share a specific target and their claims are close.

Answer one question only: should these be one decision, two separate
decisions, conflicting decisions, or is there insufficient evidence?

Use exactly one relationship:
- `merge`: the claims are semantically equivalent; the targets overlap, the
  time periods are compatible, and the evidence can be unioned without
  contradiction.
- `keep_separate`: both records can be true at the same time and describe
  different decisions.
- `conflicts`: the claims or their evidence contradict each other; never merge
  them.
- `insufficient_evidence`: the supplied evidence cannot support any of the
  above.

Rules:
1. Cite only evidence IDs printed in the EVIDENCE section. Copy the exact ID.
2. Do not invent IDs, merged claims, canonical IDs, statuses or confidence.
3. earlier_intent_id and later_intent_id must be copied exactly from the
   record headers.
4. Exact wording is not required for `merge`; semantic equivalence is. A
   negation, a changed scope, or an incompatible time period means the pair is
   not a merge.
5. Prefer `keep_separate` or `insufficient_evidence` over merging when unsure.
6. Return only JSON matching the provided schema.
"""


class DuplicateJudge:
    """Wrap the structured client with pair-specific validation."""

    def __init__(self, client: StructuredClient) -> None:
        self.client = client

    def judge(self, candidate: DuplicateCandidate) -> DuplicateVerdict:
        system, user = render_duplicate_prompt(candidate)
        result = self.client.extract(
            system=system,
            user=user,
            schema_model=DuplicateVerdict,
            schema_name="duplicate_verdict",
        )
        if not result.ok or result.data is None:
            return _fallback(
                candidate,
                "the duplicate judge failed: "
                + (result.error_text or "no structured response"),
            )
        try:
            verdict = DuplicateVerdict.model_validate(result.data)
        except ValidationError as error:
            return _fallback(
                candidate,
                f"the duplicate verdict was invalid: {error.errors()[0]}",
            )
        if (
            verdict.earlier_intent_id != candidate.earlier.id
            or verdict.later_intent_id != candidate.later.id
        ):
            return _fallback(
                candidate, "the verdict does not name this earlier/later pair"
            )
        allowed = set(candidate.allowed_evidence_ids)
        if allowed and not set(verdict.evidence_ids) <= allowed:
            return _fallback(
                candidate,
                "the verdict cites evidence outside the candidate bundle",
            )
        return verdict


def render_duplicate_prompt(
    candidate: DuplicateCandidate,
) -> tuple[str, str]:
    earlier = candidate.earlier
    later = candidate.later
    lines = [
        "Decide whether these two records are the same decision.",
        "",
        "EARLIER RECORD",
        f"id: {earlier.id}",
        f"kind: {earlier.kind.value}",
        f"pr: {earlier.pr_number}",
        f"status: {earlier.status.value}",
        f"claim: {earlier.claim}",
        f"rationale: {earlier.rationale or '(none)'}",
        f"valid_from: {_valid_from(earlier)}",
        f"valid_until: {_valid_until(earlier)}",
        "",
        "LATER RECORD",
        f"id: {later.id}",
        f"kind: {later.kind.value}",
        f"pr: {later.pr_number}",
        f"status: {later.status.value}",
        f"claim: {later.claim}",
        f"rationale: {later.rationale or '(none)'}",
        f"valid_from: {_valid_from(later)}",
        f"valid_until: {_valid_until(later)}",
        "",
        "SHARED TARGETS",
        *(f"- {key}" for key in candidate.target_keys),
        "",
        "CANDIDATE SIGNALS",
        f"- exact_normalized_claim: {candidate.exact}",
        f"- embedding_similarity: {candidate.similarity:.4f}",
        f"- temporal_compatible: {candidate.temporal_compatible}",
        "",
        "EVIDENCE",
    ]
    if candidate.evidence_lines:
        for evidence_id, text in candidate.evidence_lines:
            lines.append(f"[{evidence_id}] {text or '(text not loaded)'}")
    else:
        lines.append("(no evidence text was loaded; use the decision records)")
    lines.extend(
        [
            "",
            "Return one of: merge, keep_separate, conflicts, "
            "insufficient_evidence.",
            "Return only the JSON object required by the schema.",
        ]
    )
    return SYSTEM.strip(), "\n".join(lines)


def _valid_from(intent: IntentBase) -> str:
    scope = intent.temporal_scope
    return scope.valid_from if scope is not None else "(unknown)"


def _valid_until(intent: IntentBase) -> str:
    scope = intent.temporal_scope
    if scope is None or scope.valid_until is None:
        return "(open)"
    return scope.valid_until


def _fallback(
    candidate: DuplicateCandidate, explanation: str
) -> DuplicateVerdict:
    evidence = [
        *candidate.earlier.evidence_ids,
        *candidate.later.evidence_ids,
        *candidate.context_evidence_ids,
    ]
    return DuplicateVerdict(
        earlier_intent_id=candidate.earlier.id,
        later_intent_id=candidate.later.id,
        relationship="insufficient_evidence",
        evidence_ids=[evidence[0]] if evidence else ["unknown"],
        explanation=explanation,
    )


__all__ = ["DuplicateJudge", "render_duplicate_prompt"]
