"""The HTTP provider boundary: payload, usage capture and error mapping.

A stub ``httpx`` client keeps these offline and pins the exact JSON sent to
Groq/OpenAI, including the parts that change the quota bill (``max_tokens``,
``reasoning_effort``, structured outputs).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.core.errors import InvalidRecordError, RateLimitError, TransientUpstreamError
from app.intent.config import IntentConfig
from app.intent.llm.limits import RateLimitedProvider
from app.intent.llm.provider import OpenAICompatibleProvider, build_provider

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class StubClient:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.url: str | None = None
        self.payload: dict[str, Any] | None = None

    def post(self, url: str, *, json: dict[str, Any]) -> Any:
        self.url = url
        self.payload = json
        return self.response


def response(
    body: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    text: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=status_code,
        text=text,
        json=lambda: body if body is not None else {},
    )


def provider(client: StubClient, **overrides: Any) -> OpenAICompatibleProvider:
    options: dict[str, Any] = {
        "api_key": "gsk_test",
        "model": "openai/gpt-oss-20b",
        "base_url": "https://api.groq.com/openai/v1/",
        "timeout_seconds": 5,
        "seed": 7,
        "max_output_tokens": 900,
        "reasoning_effort": "low",
        "client": client,
    }
    options.update(overrides)
    return OpenAICompatibleProvider(**options)


def test_payload_targets_the_configured_endpoint() -> None:
    client = StubClient(response({"choices": [{"message": {"content": "{}"}}]}))
    text = provider(client).complete(
        system="sys", user="usr", schema=SCHEMA, schema_name="probe"
    )
    assert text == "{}"
    assert client.url == "https://api.groq.com/openai/v1/chat/completions"
    payload = client.payload or {}
    assert payload["model"] == "openai/gpt-oss-20b"
    assert payload["max_tokens"] == 900
    assert payload["seed"] == 7
    assert payload["reasoning_effort"] == "low"
    assert payload["temperature"] == 0
    assert payload["response_format"]["json_schema"]["strict"] is True
    assert payload["response_format"]["json_schema"]["schema"] == SCHEMA


def test_usage_is_exposed_for_the_quota_wrapper() -> None:
    usage = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    client = StubClient(
        response({"choices": [{"message": {"content": "{}"}}], "usage": usage})
    )
    instance = provider(client)
    instance.complete(system="s", user="u", schema=SCHEMA, schema_name="probe")
    assert instance.last_usage == usage


def test_a_429_becomes_a_retryable_rate_limit_error() -> None:
    client = StubClient(response(status_code=429, text="slow down"))
    with pytest.raises(RateLimitError):
        provider(client).complete(
            system="s", user="u", schema=SCHEMA, schema_name="probe"
        )


def test_a_5xx_becomes_a_transient_error() -> None:
    client = StubClient(response(status_code=503, text="down"))
    with pytest.raises(TransientUpstreamError):
        provider(client).complete(
            system="s", user="u", schema=SCHEMA, schema_name="probe"
        )


def test_a_4xx_is_not_retryable_and_keeps_only_the_error_message() -> None:
    body = {"error": {"message": "model not found"}}
    client = StubClient(
        response(body, status_code=400, text='{"error": {"message": "model not found"}}')
    )
    with pytest.raises(InvalidRecordError, match="model not found"):
        provider(client).complete(
            system="s", user="u", schema=SCHEMA, schema_name="probe"
        )


def test_an_unknown_reasoning_effort_is_rejected() -> None:
    with pytest.raises(ValueError, match="reasoning_effort"):
        provider(StubClient(response()), reasoning_effort="extreme")


def test_the_configured_provider_is_wrapped_in_the_quota_guard(tmp_path) -> None:
    config = IntentConfig(
        repo="encode/httpx",
        api_key="gsk_test",
        model="openai/gpt-oss-20b",
        budget_file=tmp_path / "llm_budget.json",
        budget_requests_per_day=5,
    )
    built = build_provider(config)
    assert isinstance(built, RateLimitedProvider)
    assert built.limits.request_budget == 5
    assert built.model == "openai/gpt-oss-20b"
