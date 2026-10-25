"""One live Groq call, opt-in only.

The ordinary suite must never touch a paid/free-tier quota, so this module
skips unless ``WHYCODE_TEST_LLM`` is set. When it does run it:

* uses the small model named by ``WHYCODE_TEST_LLM_MODEL``,
* caps output tokens and reasoning effort,
* writes to a throwaway budget ledger,
* allows exactly one request and asserts the ledger recorded one.

That makes a repeated local run cost one HTTP call, not a pipeline's worth.
"""

from __future__ import annotations

import os

import pytest

from app.intent.config import IntentConfig
from app.intent.llm.limits import DailyLedger
from app.intent.llm.provider import build_provider
from app.intent.llm.structured_client import parse_json_object

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("WHYCODE_TEST_LLM"),
        reason="set WHYCODE_TEST_LLM=1 (plus WHYCODE_TEST_LLM_API_KEY) to opt in",
    ),
]

SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _test_env(budget_file: str) -> dict[str, str]:
    api_key = os.environ.get("WHYCODE_TEST_LLM_API_KEY") or os.environ.get(
        "WHYCODE_LLM_API_KEY", ""
    )
    if not api_key:
        pytest.skip("no WHYCODE_TEST_LLM_API_KEY/WHYCODE_LLM_API_KEY in the env")
    env = dict(os.environ)
    env.update(
        {
            "WHYCODE_LLM_API_KEY": api_key,
            "WHYCODE_LLM_MODEL": os.environ.get(
                "WHYCODE_TEST_LLM_MODEL", "openai/gpt-oss-20b"
            ),
            "WHYCODE_LLM_BASE_URL": os.environ.get(
                "WHYCODE_TEST_LLM_BASE_URL",
                "https://api.groq.com/openai/v1",
            ),
            "WHYCODE_LLM_BUDGET_FILE": budget_file,
            "WHYCODE_LLM_BUDGET_REQUESTS": "1",
            "WHYCODE_LLM_BUDGET_TOKENS": "4000",
            "WHYCODE_LLM_MAX_OUTPUT_TOKENS": "200",
            "WHYCODE_LLM_REASONING_EFFORT": "low",
            # Prices the published Groq dev-plan caps; one tiny call fits.
            "WHYCODE_LLM_RPM": "30",
            "WHYCODE_LLM_TPM": "8000",
        }
    )
    return env


def test_one_live_call_returns_schema_json_and_bills_one_request(tmp_path) -> None:
    budget_file = tmp_path / "llm_budget.json"
    config = IntentConfig.from_env("whycode-live-smoke", env=_test_env(str(budget_file)))
    provider = build_provider(config)

    raw = provider.complete(
        system="Reply with a JSON object that satisfies the schema.",
        user='Return {"ok": true}.',
        schema=SCHEMA,
        schema_name="live_probe",
    )

    parsed = parse_json_object(raw)
    assert parsed is not None and parsed.get("ok") is True

    usage = DailyLedger(budget_file, key=config.model).read()
    assert usage.requests == 1
    assert 0 < usage.tokens <= config.budget_tokens_per_day
