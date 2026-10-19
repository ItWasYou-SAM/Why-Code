"""Contract for /health: typed JSON, unauthenticated, and honest —
it must NOT query Neo4j to respond, or the CI gate depends on a paid
cloud database being awake. Liveness is checked here; readiness is a
separate endpoint later, if it is ever needed."""

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client() -> TestClient:
    os.environ.setdefault("WHYCODE_GITHUB_TOKEN", "gh_test_token")
    os.environ.setdefault("WHYCODE_NEO4J_URI", "neo4j+s://0000.databases.neo4j.io")
    os.environ.setdefault("WHYCODE_NEO4J_USER", "neo4j")
    os.environ.setdefault("WHYCODE_NEO4J_PASSWORD", "not-real")
    os.environ.setdefault("WHYCODE_LLM_API_KEY", "llm-not-real")

    from app.main import create_app

    return TestClient(create_app())


def test_health_returns_200(client: TestClient) -> None:
    assert client.get("/health").status_code == 200


def test_health_payload_shape(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["service"] == "why-code"
    assert body["status"] == "ok"
    assert isinstance(body["version"], str)
    assert set(body) == {"service", "status", "version"}


def test_health_does_not_require_neo4j_being_reachable(
    client: TestClient,
) -> None:
    """No graph connection in this contract: a dead AuraDB must not turn
    the liveness endpoint into a 500."""
    body = client.get("/health").json()
    assert body["status"] == "ok"
