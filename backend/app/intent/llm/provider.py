"""LLM provider boundary.

Only this module talks HTTP. The provider receives a prompt and a JSON
schema and returns raw text; it has no tools, no memory and no ability to
write the graph. A deterministic ``StaticProvider`` exists for offline
demos and tests.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.core.errors import InvalidRecordError, RateLimitError, TransientUpstreamError
from app.intent.config import IntentConfig


class LLMProvider(Protocol):
    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str: ...


class OpenAICompatibleProvider:
    """A JSON-schema chat-completions provider.

    Works with OpenAI and any compatible endpoint. Temperature defaults to
    zero and a seed is sent when configured, because replay stability is a
    debugging contract for this component.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        temperature: float = 0.0,
        seed: int | None = 7,
        max_output_tokens: int = 2500,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("LLM api_key is required")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.temperature = temperature
        self.seed = seed
        self.max_output_tokens = max_output_tokens
        self._client = client

    @classmethod
    def from_config(cls, config: IntentConfig) -> OpenAICompatibleProvider:
        return cls(
            api_key=config.api_key,
            model=config.model,
            base_url=config.base_url,
            timeout_seconds=config.timeout_seconds,
            temperature=config.temperature,
            seed=config.seed,
            max_output_tokens=config.max_output_tokens,
        )

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        import httpx

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                },
            },
        }
        if self.seed is not None:
            payload["seed"] = self.seed

        client = self._client or httpx.Client(
            timeout=self.timeout_seconds,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        owns_client = self._client is None
        try:
            response = client.post(
                f"{self.base_url}/chat/completions", json=payload
            )
        except httpx.HTTPError as error:
            raise TransientUpstreamError(
                f"LLM request failed: {type(error).__name__}",
                component="intent",
            ) from error
        finally:
            if owns_client:
                client.close()

        if response.status_code == 429:
            raise RateLimitError(
                "LLM provider rate limit exceeded", component="intent"
            )
        if response.status_code >= 500:
            raise TransientUpstreamError(
                f"LLM provider returned {response.status_code}", component="intent"
            )
        if response.status_code >= 400:
            detail = _safe_detail(response.text)
            raise InvalidRecordError(
                f"LLM provider rejected the request ({response.status_code}): {detail}",
                component="intent",
            )
        try:
            body = response.json()
        except ValueError as error:
            raise InvalidRecordError(
                "LLM provider returned non-JSON HTTP body", component="intent"
            ) from error
        return _content_from_body(body)


class StaticProvider:
    """Return pre-recorded responses in order, keyed by schema name."""

    def __init__(
        self,
        responses: Mapping[str, str | Sequence[str]],
    ) -> None:
        self._responses = {
            key: list(value) if not isinstance(value, str) else [value]
            for key, value in responses.items()
        }
        self.calls: list[str] = []

    def complete(
        self,
        *,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
    ) -> str:
        del system, user, schema
        self.calls.append(schema_name)
        queue = self._responses.get(schema_name)
        if not queue:
            raise AssertionError(
                f"no static LLM response recorded for {schema_name!r}"
            )
        return queue.pop(0)


def build_provider(config: IntentConfig) -> LLMProvider:
    return OpenAICompatibleProvider.from_config(config)


def _content_from_body(body: Any) -> str:
    if not isinstance(body, dict):
        raise InvalidRecordError(
            "LLM provider returned a non-object response", component="intent"
        )
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise InvalidRecordError(
            "LLM provider returned no choices", component="intent"
        )
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        raise InvalidRecordError(
            "LLM provider response has no message", component="intent"
        )
    refusal = message.get("refusal")
    if isinstance(refusal, str) and refusal.strip():
        raise InvalidRecordError(
            f"LLM provider refused the request: {refusal[:300]}", component="intent"
        )
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts = [
            str(part.get("text"))
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}
        ]
        joined = "".join(parts).strip()
        if joined:
            return joined
    raise InvalidRecordError(
        "LLM provider returned empty content", component="intent"
    )


def _safe_detail(text: str) -> str:
    """Provider error bodies can echo prompts; keep only a short shape."""
    try:
        body = json.loads(text)
    except ValueError:
        return text.strip()[:200]
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str):
                return message[:200]
    return "request rejected"


__all__ = [
    "LLMProvider",
    "OpenAICompatibleProvider",
    "StaticProvider",
    "build_provider",
]
