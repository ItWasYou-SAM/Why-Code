"""One bounded LLM call to adjudicate a supersession candidate (Phase 6).

The model never discovers candidates and never writes an edge. It receives an
earlier/later pair that deterministic code already found through a shared
specific target, and answers one question: does the later evidence reverse,
revise, supersede, leave unchanged, or fail to justify a relationship?

The response is validated against :class:`SupersessionVerdict`. A verdict that
names a different pair or cites evidence outside the candidate bundle is treated
as ``insufficient_evidence`` and creates no edge.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.intent.llm.schemas import SupersessionVerdict
from app.intent.llm.structured_client import StructuredClient
from app.intent.validation.supersession import SupersessionCandidate

SYSTEM = """\
You are a deterministic adjudicator, not an agent and not a chatbot.
You receive two decisions that govern the same target. The earlier decision
was recorded first; the later evidence may or may not have changed it.

Answer one question only: does the later evidence reverse, revise, supersede,
leave unchanged, or fail to justify a relationship with the earlier decision?

Use exactly one relationship:
- `reverses`: the later evidence explicitly undoes, reverts, disables or
  removes the earlier behaviour.
- `revises`: the later evidence changes the behaviour but keeps or extends the
  earlier rationale; this is an adjustment, not a reversal.
- `supersedes`: the later evidence replaces the earlier decision with a new
  approach for the same target.
- `unrelated`: the two decisions touch the same target but do not change each
  other.
- `insufficient_evidence`: the supplied evidence cannot support any of the
  above.

Rules:
1. Cite only evidence IDs printed in the EVIDENCE section. Copy the exact ID.
2. Do not invent IDs, graph keys, statuses or confidence.
3. The earlier_decision_id and later_decision_id must be copied exactly from
   the decision headers.
4. Detection signals are deterministic hints, not conclusions. Weigh them
   against the evidence.
5. Prefer `insufficient_evidence` over guessing. No edge is created for
   `unrelated` or `insufficient_evidence`.
6. Return only JSON matching the provided schema.
"""


class SupersessionJudge:
    """Wrap the structured client with pair-specific validation."""

    def __init__(self, client: StructuredClient) -> None:
        self.client = client

    def judge(self, candidate: SupersessionCandidate) -> SupersessionVerdict:
        system, user = render_supersession_prompt(candidate)
        result = self.client.extract(
            system=system,
            user=user,
            schema_model=SupersessionVerdict,
            schema_name="supersession_verdict",
        )
        if not result.ok or result.data is None:
            return _fallback(
                candidate,
                "the supersession judge failed: "
                + (result.error_text or "no structured response"),
            )
        try:
            verdict = SupersessionVerdict.model_validate(result.data)
        except ValidationError as error:
            return _fallback(
                candidate,
                f"the supersession verdict was invalid: {error.errors()[0]}",
            )
        if (
            verdict.earlier_decision_id != candidate.earlier.id
            or verdict.later_decision_id != candidate.later.id
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


def render_supersession_prompt(
    candidate: SupersessionCandidate,
) -> tuple[str, str]:
    earlier = candidate.earlier
    later = candidate.later
    lines = [
        "Decide whether the later evidence changes the earlier decision.",
        "",
        "EARLIER DECISION",
        f"id: {earlier.id}",
        f"kind: {earlier.kind.value}",
        f"pr: {earlier.pr_number}",
        f"date: {candidate.earlier_date or 'unknown'}",
        f"status: {earlier.status.value}",
        f"claim: {earlier.claim}",
        f"rationale: {earlier.rationale or '(none)'}",
        "",
        "LATER DECISION",
        f"id: {later.id}",
        f"kind: {later.kind.value}",
        f"pr: {later.pr_number}",
        f"date: {candidate.later_date or 'unknown'}",
        f"status: {later.status.value}",
        f"claim: {later.claim}",
        f"rationale: {later.rationale or '(none)'}",
        "",
        "SHARED TARGETS",
        *(f"- {key}" for key in candidate.targets),
        "",
        "DETECTION SIGNALS",
        *(f"- {signal}" for signal in candidate.signals),
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
            "Return one of: reverses, revises, supersedes, unrelated, "
            "insufficient_evidence.",
            "Return only the JSON object required by the schema.",
        ]
    )
    return SYSTEM.strip(), "\n".join(lines)


def _fallback(
    candidate: SupersessionCandidate, explanation: str
) -> SupersessionVerdict:
    evidence = [*candidate.earlier.evidence_ids, *candidate.later.evidence_ids]
    return SupersessionVerdict(
        earlier_decision_id=candidate.earlier.id,
        later_decision_id=candidate.later.id,
        relationship="insufficient_evidence",
        evidence_ids=[evidence[0]] if evidence else ["unknown"],
        explanation=explanation,
    )


__all__ = ["SupersessionJudge", "render_supersession_prompt"]
