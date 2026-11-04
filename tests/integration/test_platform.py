"""Health check, error pages, security headers and migrations."""

import pytest
from alembic import command
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from hourplan import create_app
from tests.conftest import alembic_config


def test_healthz_reports_ok(client: FlaskClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json == {"status": "ok", "database": "ok"}


def test_healthz_reports_database_failure(
    client: FlaskClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: Session, *args: object, **kwargs: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("down"))

    monkeypatch.setattr(Session, "execute", broken)

    response = client.get("/healthz")

    assert response.status_code == 503
    assert response.json == {"status": "error", "database": "unavailable"}


def test_not_found_page_is_html(client: FlaskClient) -> None:
    response = client.get("/missing")

    assert response.status_code == 404
    assert "Página no encontrada" in response.get_data(as_text=True)


def test_not_found_is_json_for_api_clients(client: FlaskClient) -> None:
    response = client.get("/missing", headers={"Accept": "application/json"})

    assert response.status_code == 404
    assert response.json == {"error": "Not Found", "status": 404}


def test_unexpected_error_hides_traceback(app: Flask) -> None:
    @app.get("/boom")
    def boom() -> str:
        raise RuntimeError("secret detail")

    response = app.test_client().get("/boom")

    body = response.get_data(as_text=True)
    assert response.status_code == 500
    assert "secret detail" not in body
    assert "Error interno" in body


def test_security_headers_are_set(client: FlaskClient) -> None:
    headers = client.get("/login").headers

    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "unsafe" not in headers["Content-Security-Policy"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "Strict-Transport-Security" not in headers


def test_production_sends_hsts(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    app = create_app("production")

    headers = app.test_client().get("/healthz").headers

    assert headers["Strict-Transport-Security"].startswith("max-age=")


def test_models_match_migrations(engine: Engine) -> None:
    command.check(alembic_config(engine.url.render_as_string(hide_password=False)))
