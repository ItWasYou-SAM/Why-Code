"""Settings loaded once from .env and injected; never constructed ad hoc.

Secrets are ``repr=False`` so an incident log can include the settings
object without leaking tokens.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

_ENV_PREFIX = "WHYCODE_"
_REQUIRED_FIELDS = (
    "github_token",
    "neo4j_uri",
    "neo4j_user",
    "neo4j_password",
    "llm_api_key",
)


class Settings(BaseModel):
    """One immutable view of the environment for a single process."""

    github_token: str = Field(repr=False)
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str = Field(repr=False)
    neo4j_database: str = "neo4j"
    llm_api_key: str = Field(repr=False)
    llm_model: str = "gpt-4o-mini"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_timeout_seconds: float = 60.0
    artifacts_dir: Path = Path("artifacts")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        missing: list[str] = []
        found: dict[str, str] = {}
        for field_name in _REQUIRED_FIELDS:
            env_name = f"{_ENV_PREFIX}{field_name.upper()}"
            value = source.get(env_name, "").strip()
            if not value:
                missing.append(env_name)
            else:
                found[field_name] = value
        if missing:
            raise ValueError(f"missing required environment variables: {', '.join(missing)}")
        return cls(
            github_token=found["github_token"],
            neo4j_uri=found["neo4j_uri"],
            neo4j_user=found["neo4j_user"],
            neo4j_password=found["neo4j_password"],
            neo4j_database=source.get("WHYCODE_NEO4J_DATABASE", "neo4j") or "neo4j",
            llm_api_key=found["llm_api_key"],
            llm_model=source.get("WHYCODE_LLM_MODEL", "gpt-4o-mini") or "gpt-4o-mini",
            llm_base_url=(
                source.get("WHYCODE_LLM_BASE_URL", "https://api.openai.com/v1")
                or "https://api.openai.com/v1"
            ),
            llm_timeout_seconds=float(
                source.get("WHYCODE_LLM_TIMEOUT_SECONDS", "60") or "60"
            ),
            artifacts_dir=Path(source.get("WHYCODE_ARTIFACTS_DIR", "artifacts")),
        )
