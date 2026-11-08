"""Configuration classes, read from environment variables."""

import os
from datetime import timedelta
from typing import Any, ClassVar


class ConfigError(RuntimeError):
    """A required setting is missing or invalid."""


def _float_env(name: str) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError as err:
        raise ConfigError(f"{name} debe ser un número.") from err


class BaseConfig:
    REQUIRED: ClassVar[tuple[str, ...]] = ("SECRET_KEY", "DATABASE_URL")

    DEBUG = False
    TESTING = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    REMEMBER_COOKIE_SECURE = True
    REMEMBER_COOKIE_DURATION = timedelta(days=14)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=12)

    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    WTF_CSRF_TIME_LIMIT = None

    # Voice dictation is off unless an STT server is configured (see ADR-006).
    VOICE_STT_URL: str | None = None
    VOICE_STT_MODEL = "whisper-small"
    VOICE_LLM_URL: str | None = None
    VOICE_LLM_MODEL = "local"
    VOICE_STT_TIMEOUT = 300.0
    VOICE_LLM_TIMEOUT = 90.0
    VOICE_MAX_SECONDS = 180.0
    VOICE_UPLOAD_LIMIT = "20 per hour"

    RATELIMIT_HEADERS_ENABLED = True
    LOGIN_RATE_LIMIT = "5 per minute"

    SECURITY_HSTS = True

    def as_mapping(self) -> dict[str, Any]:
        values = {key: getattr(self, key) for key in dir(self) if key.isupper()}
        values.update(self._from_env())
        missing = [key for key in self.REQUIRED if not values.get(key)]
        if missing:
            raise ConfigError(f"Missing required settings: {', '.join(missing)}")
        return values

    def _from_env(self) -> dict[str, Any]:
        env = {
            "SECRET_KEY": os.environ.get("SECRET_KEY"),
            "DATABASE_URL": os.environ.get("DATABASE_URL"),
            "RATELIMIT_STORAGE_URI": os.environ.get("RATELIMIT_STORAGE_URI", "memory://"),
            "HOURPLAN_OVERLAY_DIR": os.environ.get("HOURPLAN_OVERLAY_DIR"),
            "VOICE_STT_URL": os.environ.get("VOICE_STT_URL"),
            "VOICE_STT_MODEL": os.environ.get("VOICE_STT_MODEL"),
            "VOICE_LLM_URL": os.environ.get("VOICE_LLM_URL"),
            "VOICE_LLM_MODEL": os.environ.get("VOICE_LLM_MODEL"),
            "VOICE_STT_TIMEOUT": _float_env("VOICE_STT_TIMEOUT"),
            "VOICE_LLM_TIMEOUT": _float_env("VOICE_LLM_TIMEOUT"),
            "VOICE_MAX_SECONDS": _float_env("VOICE_MAX_SECONDS"),
        }
        return {key: value for key, value in env.items() if value is not None}


class ProductionConfig(BaseConfig):
    pass


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False
    SECURITY_HSTS = False

    def _from_env(self) -> dict[str, Any]:
        env = super()._from_env()
        env.setdefault("SECRET_KEY", "dev-only-not-secret")
        return env


class TestingConfig(DevelopmentConfig):
    DEBUG = False
    TESTING = True

    def _from_env(self) -> dict[str, Any]:
        env = super()._from_env()
        if test_url := os.environ.get("TEST_DATABASE_URL"):
            env["DATABASE_URL"] = test_url
        env["RATELIMIT_STORAGE_URI"] = "memory://"
        return env


CONFIGS: dict[str, type[BaseConfig]] = {
    "production": ProductionConfig,
    "development": DevelopmentConfig,
    "testing": TestingConfig,
}


def load_config(name: str | None = None) -> dict[str, Any]:
    """Return the settings for ``name`` (defaults to ``HOURPLAN_ENV``, then production)."""
    config_name = name or os.environ.get("HOURPLAN_ENV", "production")
    try:
        config_class = CONFIGS[config_name]
    except KeyError as err:
        raise ConfigError(f"Unknown configuration: {config_name!r}") from err
    return config_class().as_mapping()
