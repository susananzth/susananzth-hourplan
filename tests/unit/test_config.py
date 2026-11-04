import pytest

from hourplan.config import ConfigError, load_config


def test_production_fails_without_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x/y")

    with pytest.raises(ConfigError, match="SECRET_KEY"):
        load_config("production")


def test_production_fails_without_database_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "s")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ConfigError, match="DATABASE_URL"):
        load_config("production")


def test_defaults_to_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOURPLAN_ENV", raising=False)
    monkeypatch.setenv("SECRET_KEY", "s")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x/y")

    config = load_config()

    assert config["DEBUG"] is False
    assert config["SESSION_COOKIE_SECURE"] is True
    assert config["SECURITY_HSTS"] is True


def test_rejects_unknown_configuration() -> None:
    with pytest.raises(ConfigError, match="Unknown configuration"):
        load_config("staging")


def test_development_has_a_local_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SECRET_KEY", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x/y")

    config = load_config("development")

    assert config["SECRET_KEY"]
    assert config["SESSION_COOKIE_SECURE"] is False


def test_testing_prefers_test_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://x/main")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql+psycopg://x/test")

    config = load_config("testing")

    assert config["DATABASE_URL"].endswith("/test")
    assert config["TESTING"] is True
