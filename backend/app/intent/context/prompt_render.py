"""Prompt rendering for the extraction calls.

The prompts are deliberately narrow. They describe a single transformation,
forbid model-invented confidence/graph links, and repeat that only the IDs
inside ``EVIDENCE`` may be cited. The JSON shape itself is enforced by the
provider's structured-output mode and by Pydantic, not by prose.
"""

from __future__ import annotations

from enum import StrEnum

from app.intent.context.context_packer import EvidencePack

_BASE_RULES = """\
You are a deterministic extraction step, not an agent and not a chatbot.
You receive one pull request's evidence pack and return only JSON matching
the provided schema.

Non-negotiable rules:
1. Cite only evidence IDs printed in the EVIDENCE section. Copy either the
   [E#] label or the exact record ID printed beside it. Never invent, shorten,
   or transform an ID.
2. Every candidate must cite at least one evidence ID. If the evidence does
   not support a candidate, return an empty candidates list.
3. Use `decision_type` and `status` only as the classification labels defined
   by the schema. Do not output confidence, supersession links, graph keys,
   database identifiers, or any field the schema does not define.
4. Every target_id must be copied from the TARGETS section, the changed-file
   list, or the supplied code regions. Never guess a file, symbol, region, or
   config key.
5. A claim is one atomic sentence stating a decision, constraint, or reversal
   that the evidence explicitly supports. Do not merge two claims into one.
6. Prefer quoted rationale from review comments, commits, PR bodies, issues
   and docs. Do not speculate about an author's unstated intent.
7. If the PR only restates code without explaining why, return no candidates.
   Do not infer intent from code style alone.
8. A refactor without a stated rationale is not a decision.
9. A code comment alone is weak evidence. Classify such a claim as `uncertain`
   unless another source corroborates it.
10. A merged PR with an explicit maintainer rationale may be `accepted`.
11. A rejected (unmerged) PR produces a `rejected` decision or constraint, not
    an active decision.
12. A reverted PR must state the reversal and cite the revert evidence. Code
    will create the original-decision and REVERSES relationship.
13. If the supplied evidence is insufficient, do not guess: return an empty
    candidates list and explain the gap in `missing_evidence`.
14. Emit at most five candidates. Quality matters more than count.
"""

_SYSTEM = {
    "decision": _BASE_RULES
    + """
Focus: the *why* behind a merged change. A decision explains an engineering
choice that was made (for example a default, a retry policy, an API shape,
or a removed behaviour). Use `design_decision`, `api_contract`,
`operational_policy`, or `incident_response` for ordinary decisions, and the
dedicated `constraint` / `reversal` types when those apply. Do not restate
what the diff does unless the evidence also explains why.
""",
    "constraint": _BASE_RULES
    + """
Focus: explicit limits, requirements, invariants, compatibility rules, or
security constraints that the change must satisfy. A constraint must be
stated or clearly enforced by the supplied evidence. Do not infer a generic
best practice.
""",
    "reversal": _BASE_RULES
    + """
Focus: a later change that reverts, removes, deprecates, replaces, or rolls
back earlier behaviour. State what was reversed and why the evidence says
so. Do not claim a supersession link; code will detect it deterministically.
""",
}

_USER = {
    "decision": "Extract evidence-linked decision candidates from this pack.",
    "constraint": "Extract evidence-linked constraint candidates from this pack.",
    "reversal": "Extract evidence-linked reversal candidates from this pack.",
}


class ExtractionKind(StrEnum):
    DECISION = "decision"
    CONSTRAINT = "constraint"
    REVERSAL = "reversal"


def render_prompt(kind: ExtractionKind | str, pack: EvidencePack) -> tuple[str, str]:
    key = kind.value if isinstance(kind, ExtractionKind) else str(kind)
    if key not in _SYSTEM:
        raise ValueError(f"unknown extraction kind {key!r}")
    system = _SYSTEM[key].strip()
    user = (
        f"{_USER[key]}\n\n"
        f"{pack.text()}\n\n"
        "Return only the JSON object required by the schema."
    )
    return system, user


__all__ = ["ExtractionKind", "render_prompt"]
