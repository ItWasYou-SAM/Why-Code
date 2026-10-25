"""Fixture catalog for retrieval Phase 1 tests and offline demos."""

from __future__ import annotations

from app.retrieval.question.entity_catalog import StaticEntityCatalog
from app.retrieval.question.models import CatalogEntity, EntityType

REPO = "encode/httpx"
COMMIT = "a" * 40

CONFIG_RETRIES_KEY = "httpx:config:httpx/_config.py:retries"
CONFIG_MAX_ATTEMPTS_KEY = "httpx:config:httpx/_config.py:retry.max_attempts"
CONFIG_TIMEOUT_KEY = "httpx:config:httpx/_config.py:timeout"
SYMBOL_SEND_KEY = (
    f"httpx:symbol:httpx/_client.py@{COMMIT}:Client.send"
)
FILE_CLIENT_KEY = f"httpx:file:httpx/_client.py@{COMMIT}"
DECISION_DEFAULT_KEY = "httpx:decision:retry-default-disabled"
DECISION_CONNECTION_KEY = "httpx:decision:retry-connection-only"
DECISION_REVERSAL_KEY = "httpx:decision:retry-behaviour-reversed"
PERSON_ALICE_KEY = "httpx:person:alice"


def catalog_entities() -> list[CatalogEntity]:
    return [
        CatalogEntity(
            graph_key=CONFIG_RETRIES_KEY,
            type=EntityType.CONFIG_KEY,
            identifier="retries",
            path="httpx/_config.py",
            aliases=("retry",),
        ),
        CatalogEntity(
            graph_key=CONFIG_MAX_ATTEMPTS_KEY,
            type=EntityType.CONFIG_KEY,
            identifier="retry.max_attempts",
            path="httpx/_config.py",
            aliases=("max_attempts",),
        ),
        CatalogEntity(
            graph_key=CONFIG_TIMEOUT_KEY,
            type=EntityType.CONFIG_KEY,
            identifier="timeout",
            path="httpx/_config.py",
        ),
        CatalogEntity(
            graph_key=SYMBOL_SEND_KEY,
            type=EntityType.SYMBOL,
            identifier="httpx._client.Client.send",
            display_name="send",
            path="httpx/_client.py",
            aliases=("Client.send",),
        ),
        CatalogEntity(
            graph_key=FILE_CLIENT_KEY,
            type=EntityType.FILE,
            identifier="httpx/_client.py",
            path="httpx/_client.py",
            aliases=("_client.py",),
        ),
        CatalogEntity(
            graph_key=DECISION_DEFAULT_KEY,
            type=EntityType.DECISION,
            identifier="dec_retry_default",
            claim=(
                "Automatic retries are disabled by default; callers must "
                "opt in."
            ),
            status="accepted",
            pr_number=1525,
            source_label="Decision",
        ),
        CatalogEntity(
            graph_key=DECISION_CONNECTION_KEY,
            type=EntityType.DECISION,
            identifier="dec_retry_connection_only",
            claim=(
                "Retries are limited to connection failures; retrying reads "
                "or writes can duplicate side effects."
            ),
            status="accepted",
            pr_number=1419,
            source_label="Decision",
        ),
        CatalogEntity(
            graph_key=DECISION_REVERSAL_KEY,
            type=EntityType.DECISION,
            identifier="dec_retry_reversal",
            claim=(
                "The default retry behaviour was reversed in favour of "
                "explicit opt-in retries."
            ),
            status="reversed",
            pr_number=1525,
            source_label="Reversal",
        ),
        CatalogEntity(
            graph_key=PERSON_ALICE_KEY,
            type=EntityType.PERSON,
            identifier="alice",
            display_name="alice",
        ),
    ]


def static_catalog() -> StaticEntityCatalog:
    return StaticEntityCatalog(REPO, catalog_entities())


__all__ = [
    "COMMIT",
    "CONFIG_MAX_ATTEMPTS_KEY",
    "CONFIG_RETRIES_KEY",
    "CONFIG_TIMEOUT_KEY",
    "DECISION_CONNECTION_KEY",
    "DECISION_DEFAULT_KEY",
    "DECISION_REVERSAL_KEY",
    "FILE_CLIENT_KEY",
    "PERSON_ALICE_KEY",
    "REPO",
    "SYMBOL_SEND_KEY",
    "catalog_entities",
    "static_catalog",
]
