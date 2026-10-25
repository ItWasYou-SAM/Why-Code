"""Intent-extraction configuration.

Selection is deterministic and separate from the LLM. Every threshold is a
named field so a run can explain why a PR was sent to the model (D-0039).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from app.intent.llm.limits import ProviderLimits


class IntentConfig(BaseModel):
    repo: str
    artifacts_dir: Path = Path("artifacts")
    # Candidate detection.
    decision_score_threshold: int = 4
    max_prs: int | None = None
    # Phase 7: embedding similarity is only a candidate-pair generator; the
    # LLM adjudicator decides whether the pair may merge.
    duplicate_similarity_threshold: float = 0.70
    # Context packing.
    max_context_tokens: int = 6000
    max_target_options: int = 80
    max_diff_hunks: int = 6
    max_diff_lines_per_hunk: int = 40
    max_doc_characters: int = 4000
    max_regions: int = 4
    max_review_comments: int = 8
    max_reviews: int = 3
    max_commit_messages: int = 3
    max_issues: int = 3
    max_issue_comments: int = 4
    max_docs: int = 3
    max_code_lines: int = 24
    # LLM boundary.
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = Field(default="", repr=False)
    # Groq's gpt-oss models accept low/medium/high; reasoning tokens are
    # billed as output, so tests pin "low" to stretch the daily budget.
    reasoning_effort: str | None = None
    # Provider caps (defaults: Groq dev plan, openai/gpt-oss-20b/120b).
    requests_per_minute: int = 30
    requests_per_day: int = 1000
    tokens_per_minute: int = 8000
    tokens_per_day: int = 200_000
    # Optional, stricter self-imposed daily ceilings. None = provider cap.
    budget_requests_per_day: int | None = None
    budget_tokens_per_day: int | None = None
    budget_file: Path | None = None
    # Phase 8 write contract: which extraction version produced the intents.
    extraction_version: str = "1"
    temperature: float = 0.0
    seed: int = 7
    max_output_tokens: int = 2500
    max_attempts: int = 2
    retry_base_delay: float = 0.5
    timeout_seconds: float = 60.0
    # Validation.
    require_graph_evidence: bool = True
    # Phase 4: a partially resolved target set becomes an observation; with
    # this disabled, any unresolved target rejects the candidate.
    allow_target_downgrade: bool = True
    reject_unsupported_participants: bool = False
    # Artifacts.
    intent_dir: Path | None = None

    @classmethod
    def from_env(
        cls,
        repo: str,
        *,
        artifacts_dir: str | Path | None = None,
        env: dict[str, str] | None = None,
    ) -> IntentConfig:
        source = os.environ if env is None else env
        artifacts = Path(
            artifacts_dir
            or source.get("WHYCODE_ARTIFACTS_DIR", "artifacts")
        )
        budget_file = source.get("WHYCODE_LLM_BUDGET_FILE", "").strip()
        return cls(
            repo=repo,
            artifacts_dir=artifacts,
            model=source.get("WHYCODE_LLM_MODEL", "gpt-4o-mini")
            or "gpt-4o-mini",
            extraction_version=(
                source.get("WHYCODE_INTENT_EXTRACTION_VERSION", "1") or "1"
            ),
            base_url=source.get(
                "WHYCODE_LLM_BASE_URL", "https://api.openai.com/v1"
            )
            or "https://api.openai.com/v1",
            api_key=source.get("WHYCODE_LLM_API_KEY", ""),
            reasoning_effort=(
                source.get("WHYCODE_LLM_REASONING_EFFORT", "").strip() or None
            ),
            requests_per_minute=_positive_int(
                source.get("WHYCODE_LLM_RPM"), 30
            ),
            requests_per_day=_positive_int(
                source.get("WHYCODE_LLM_RPD"), 1000
            ),
            tokens_per_minute=_positive_int(
                source.get("WHYCODE_LLM_TPM"), 8000
            ),
            tokens_per_day=_positive_int(
                source.get("WHYCODE_LLM_TPD"), 200_000
            ),
            budget_requests_per_day=_optional_int(
                source.get("WHYCODE_LLM_BUDGET_REQUESTS")
            ),
            budget_tokens_per_day=_optional_int(
                source.get("WHYCODE_LLM_BUDGET_TOKENS")
            ),
            budget_file=Path(budget_file) if budget_file else None,
            temperature=float(source.get("WHYCODE_LLM_TEMPERATURE", "0") or "0"),
            seed=int(source.get("WHYCODE_LLM_SEED", "7") or "7"),
            max_output_tokens=int(
                source.get("WHYCODE_LLM_MAX_OUTPUT_TOKENS", "2500") or "2500"
            ),
            timeout_seconds=float(
                source.get("WHYCODE_LLM_TIMEOUT_SECONDS", "60") or "60"
            ),
            max_context_tokens=int(
                source.get("WHYCODE_INTENT_MAX_CONTEXT_TOKENS", "6000") or "6000"
            ),
            decision_score_threshold=int(
                source.get("WHYCODE_INTENT_SCORE_THRESHOLD", "4") or "4"
            ),
            duplicate_similarity_threshold=float(
                source.get("WHYCODE_INTENT_DUPLICATE_THRESHOLD", "0.70")
                or "0.70"
            ),
        )

    def resolve_intent_dir(self) -> Path:
        return self.intent_dir or self.artifacts_dir / "intent"

    def resolve_budget_file(self) -> Path:
        """Where the daily LLM quota ledger lives."""
        return self.budget_file or self.artifacts_dir / "intent" / "llm_budget.json"

    def limits(self) -> ProviderLimits:
        """The published caps plus this run's tighter daily ceilings."""
        from app.intent.llm.limits import ProviderLimits

        return ProviderLimits(
            requests_per_minute=self.requests_per_minute,
            requests_per_day=self.requests_per_day,
            tokens_per_minute=self.tokens_per_minute,
            tokens_per_day=self.tokens_per_day,
            min_interval_seconds=60.0 / self.requests_per_minute,
            max_requests_per_day=self.budget_requests_per_day,
            max_tokens_per_day=self.budget_tokens_per_day,
        )


def _positive_int(raw: str | None, default: int) -> int:
    value = (raw or "").strip()
    return int(value) if value else default


def _optional_int(raw: str | None) -> int | None:
    value = (raw or "").strip()
    return int(value) if value else None


__all__ = ["IntentConfig"]
