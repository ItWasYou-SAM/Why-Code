"""Prompts for the answer model.

The system prompt is a contract, not a suggestion: synthesis only, no
research, no outside knowledge, citations mandatory.
"""

from __future__ import annotations

from typing import Any

from app.retrieval.context.evidence_packer import EvidencePack

ANSWER_SYSTEM_PROMPT = """You are a codebase decision analyst.

You answer questions about why code is the way it is, using only the supplied
decision graph and evidence.

Rules:
1. Use only the supplied evidence.
2. Cite every factual statement with evidence IDs.
3. If evidence conflicts, explain the conflict.
4. If a decision was reversed or revised, say so.
5. If evidence is insufficient, return INSUFFICIENT_EVIDENCE.
6. Never invent file names, PR numbers, people, dates, or quotes.
7. Do not use knowledge outside the supplied context.
8. Be concise and technical."""

# The provider-facing contract. It is deliberately decoupled from
# ``AnswerDraft.model_json_schema()``: generation is constrained to this
# shape, while the response is still parsed and validated by Pydantic. Every
# property is required and no extra property is allowed, which is what makes
# the structured-output call a shape constraint rather than a suggestion.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "answer_status",
        "answer",
        "citations",
        "caveats",
        "confidence",
    ],
    "properties": {
        "answer_status": {
            "type": "string",
            "enum": ["answered", "insufficient_evidence", "contested"],
        },
        "answer": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
        "caveats": {"type": "array", "items": {"type": "string"}},
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low", "none"],
        },
    },
}


def build_user_prompt(
    pack: EvidencePack, *, strict: bool = False, max_answer_tokens: int = 700
) -> str:
    parts = [
        "Using only the evidence pack, answer:",
        "",
        f"Question: {pack.question}",
        "",
        "EVIDENCE PACK:",
        pack.text,
        "",
        "Return JSON with:",
        "- answer_status: answered | insufficient_evidence | contested",
        "- answer",
        "- citations: list of evidence IDs used (for example [E1])",
        "- caveats",
        "- confidence: high | medium | low | none",
        f"- Keep the answer under approximately {max_answer_tokens} tokens.",
    ]
    if strict:
        parts.extend(
            [
                "",
                "STRICT CORRECTION: the previous response failed validation.",
                "Every sentence in the answer must cite at least one [E#] from the evidence pack.",
                "Do not cite any ID that is not present in the pack.",
                "Do not quote text that is not an exact excerpt.",
                "If you cannot do that, return answer_status=insufficient_evidence.",
            ]
        )
    return "\n".join(parts)


__all__ = ["ANSWER_SCHEMA", "ANSWER_SYSTEM_PROMPT", "build_user_prompt"]
