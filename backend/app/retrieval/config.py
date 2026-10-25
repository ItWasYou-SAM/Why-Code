"""Retrieval configuration.

Every threshold and budget is a named field so a run can explain why an
entity was accepted, why the LLM was consulted, or why a question was passed
to the next retrieval stage without an entity. The LLM boundary is optional:
deterministic question understanding must work without an API key.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Literal

from pydantic import BaseModel, Field


class RetrievalConfig(BaseModel):
    """One immutable view of the retrieval environment for a repository."""

    repo: str | None = None

    # Question understanding.
    max_entities: int = Field(default=6, ge=1, le=12)
    max_catalog_options: int = Field(default=40, ge=1, le=100)
    max_question_chars: int = Field(default=2000, ge=1, le=4000)
    lexical_accept_score: float = Field(default=0.65, ge=0.0, le=1.0)
    lexical_accept_margin: float = Field(default=0.10, ge=0.0, le=1.0)
    min_candidate_score: float = Field(default=0.40, ge=0.0, le=1.0)
    fallback_score: float = Field(default=0.50, ge=0.0, le=1.0)
    fulltext_max_terms: int = Field(default=12, ge=1, le=40)
    catalog_limit_per_type: int = Field(default=5000, ge=1)

    # Retrieval sources and fusion.
    exact_limit: int = Field(default=10, ge=1)
    fulltext_limit: int = Field(default=20, ge=1)
    vector_limit: int = Field(default=20, ge=1)
    max_expansion_candidates: int = Field(default=5, ge=1)
    k: int = Field(default=60, ge=1)
    exact_weight: float = Field(default=5.0, ge=0.0)
    fulltext_weight: float = Field(default=2.0, ge=0.0)
    vector_weight: float = Field(default=1.5, ge=0.0)
    graph_weight: float = Field(default=2.5, ge=0.0)
    parallel_retrieval: bool = True

    # Reranking bonuses and penalties.
    max_decisions: int = Field(default=5, ge=1)
    graph_path_bonus: float = Field(default=0.20, ge=0.0)
    multi_evidence_bonus: float = Field(default=0.12, ge=0.0)
    active_bonus: float = Field(default=0.05, ge=0.0)
    review_anchor_bonus: float = Field(default=0.10, ge=0.0)
    target_match_bonus: float = Field(default=0.15, ge=0.0)
    superseded_penalty: float = Field(default=0.25, ge=0.0)
    unrelated_target_penalty: float = Field(default=0.20, ge=0.0)
    single_weak_evidence_penalty: float = Field(default=0.08, ge=0.0)
    stale_without_check_penalty: float = Field(default=0.05, ge=0.0)
    stale_evidence_days: int = Field(default=365, ge=1)

    # Answer packing and validation.
    max_evidence_items: int = Field(default=8, ge=1, le=20)
    max_answer_tokens: int = Field(default=700, ge=100, le=4000)
    max_context_tokens: int = Field(default=6000, ge=100)
    max_excerpt_chars: int = Field(default=600, ge=1)
    min_high_confidence_evidence: int = Field(default=2, ge=1)
    answer_attempts: int = Field(default=2, ge=1)

    # Indexes.
    embedding_dimensions: int = Field(default=1536, gt=0)
    vector_similarity: Literal["cosine", "euclidean", "dot"] = "cosine"
    max_embedding_chars: int = Field(default=8000, ge=1, le=100_000)

    # LLM boundary. The key is optional: without it, extraction is
    # deterministic and the LLM fallback is skipped with a warning.
    enable_llm_entity_extraction: bool = True
    llm_api_key: str = Field(default="", repr=False)
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 60.0
    llm_temperature: float = 0.0
    llm_seed: int = 7
    llm_max_output_tokens: int = 500
    llm_max_attempts: int = 1
    llm_retry_base_delay: float = 0.0

    @classmethod
    def from_env(
        cls,
        repo: str,
        *,
        env: Mapping[str, str] | None = None,
    ) -> RetrievalConfig:
        source = os.environ if env is None else env
        return cls(
            repo=repo,
            max_entities=_env_int(source, "WHYCODE_RETRIEVAL_MAX_ENTITIES", 6),
            max_catalog_options=_env_int(
                source, "WHYCODE_RETRIEVAL_MAX_CATALOG_OPTIONS", 40
            ),
            max_question_chars=_env_int(
                source, "WHYCODE_RETRIEVAL_MAX_QUESTION_CHARS", 2000
            ),
            lexical_accept_score=_env_float(
                source, "WHYCODE_RETRIEVAL_LEXICAL_ACCEPT_SCORE", 0.65
            ),
            lexical_accept_margin=_env_float(
                source, "WHYCODE_RETRIEVAL_LEXICAL_ACCEPT_MARGIN", 0.10
            ),
            min_candidate_score=_env_float(
                source, "WHYCODE_RETRIEVAL_MIN_CANDIDATE_SCORE", 0.40
            ),
            fallback_score=_env_float(
                source, "WHYCODE_RETRIEVAL_FALLBACK_SCORE", 0.50
            ),
            fulltext_max_terms=_env_int(
                source, "WHYCODE_RETRIEVAL_FULLTEXT_MAX_TERMS", 12
            ),
            catalog_limit_per_type=_env_int(
                source, "WHYCODE_RETRIEVAL_CATALOG_LIMIT_PER_TYPE", 5000
            ),
            stale_evidence_days=_env_int(
                source, "WHYCODE_RETRIEVAL_STALE_EVIDENCE_DAYS", 365
            ),
            enable_llm_entity_extraction=_env_bool(
                source, "WHYCODE_RETRIEVAL_ENABLE_LLM", True
            ),
            llm_api_key=source.get("WHYCODE_LLM_API_KEY", "").strip(),
            llm_model=source.get("WHYCODE_LLM_MODEL", "gpt-4o-mini")
            or "gpt-4o-mini",
            llm_base_url=source.get(
                "WHYCODE_LLM_BASE_URL", "https://api.openai.com/v1"
            )
            or "https://api.openai.com/v1",
            llm_timeout_seconds=_env_float(
                source, "WHYCODE_LLM_TIMEOUT_SECONDS", 60.0
            ),
            llm_temperature=_env_float(
                source, "WHYCODE_LLM_TEMPERATURE", 0.0
            ),
            llm_seed=_env_int(source, "WHYCODE_LLM_SEED", 7),
            llm_max_output_tokens=_env_int(
                source, "WHYCODE_RETRIEVAL_LLM_MAX_OUTPUT_TOKENS", 500
            ),
        )

    @property
    def llm_available(self) -> bool:
        return self.enable_llm_entity_extraction and bool(
            self.llm_api_key.strip()
        )

    def for_request(
        self,
        *,
        max_evidence_items: int,
        max_answer_tokens: int,
    ) -> RetrievalConfig:
        return self.model_copy(
            update={
                "max_evidence_items": max_evidence_items,
                "max_answer_tokens": max_answer_tokens,
            }
        )


def _env_number[Number: (int, float)](
    source: Mapping[str, str],
    name: str,
    default: Number,
    convert: Callable[[str], Number],
) -> Number:
    value = source.get(name, "").strip()
    if not value:
        return default
    try:
        return convert(value)
    except ValueError:
        return default


def _env_int(source: Mapping[str, str], name: str, default: int) -> int:
    return _env_number(source, name, default, int)


def _env_float(source: Mapping[str, str], name: str, default: float) -> float:
    return _env_number(source, name, default, float)


def _env_bool(source: Mapping[str, str], name: str, default: bool) -> bool:
    value = source.get(name, "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


__all__ = ["RetrievalConfig"]
