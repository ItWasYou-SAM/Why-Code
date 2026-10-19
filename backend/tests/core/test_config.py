"""Contract: settings load once, fail loudly on missing required vars,
and are injected — never constructed ad hoc inside components."""

import pytest

REQUIRED_ENV = {
    "WHYCODE_GITHUB_TOKEN": "gh_test_token",
    "WHYCODE_NEO4J_URI": "neo4j+s://0000.databases.neo4j.io",
    "WHYCODE_NEO4J_USER": "neo4j",
    "WHYCODE_NEO4J_PASSWORD": "not-real",
    "WHYCODE_LLM_API_KEY": "llm-not-real",
}


@pytest.fixture()
def full_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("WHYCODE_ARTIFACTS_DIR", "/tmp/whycode-artifacts-test")


def test_settings_load_from_required_env_vars(full_env: None) -> None:
    from app.core.config import Settings

    settings = Settings.from_env()
    assert settings.github_token == "gh_test_token"
    assert settings.neo4j_uri == "neo4j+s://0000.databases.neo4j.io"
    assert settings.neo4j_user == "neo4j"
    assert settings.neo4j_password == "not-real"
    assert settings.llm_api_key == "llm-not-real"


def test_settings_neo4j_database_defaults_to_neo4j(full_env: None) -> None:
    from app.core.config import Settings

    assert Settings.from_env().neo4j_database == "neo4j"


def test_settings_fail_on_missing_required_var(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import Settings

    monkeypatch.delenv("WHYCODE_GITHUB_TOKEN", raising=False)
    for key, value in REQUIRED_ENV.items():
        if key != "WHYCODE_GITHUB_TOKEN":
            monkeypatch.setenv(key, value)
    with pytest.raises(Exception, match="WHYCODE_GITHUB_TOKEN"):
        Settings.from_env()


def test_settings_never_secret_loggable() -> None:
    """repr must not leak secrets: logs are shared on incident threads."""
    import os

    from app.core.config import Settings

    for key, value in REQUIRED_ENV.items():
        os.environ[key] = value
    try:
        settings = Settings.from_env()
    finally:
        for key in REQUIRED_ENV:
            os.environ.pop(key, None)
    assert "not-real" not in repr(settings)
    assert "gh_test_token" not in repr(settings)
