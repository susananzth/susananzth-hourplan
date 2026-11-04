import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from flask import Flask
from flask.testing import FlaskClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker
from werkzeug.test import TestResponse

from hourplan import create_app
from hourplan.auth import services
from hourplan.auth.models import User
from hourplan.extensions import db, limiter

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "correct horse battery staple"
CSRF_RE = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


def _database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        raise pytest.UsageError("Set TEST_DATABASE_URL (PostgreSQL) to run the tests")
    return url


def pytest_configure(config: pytest.Config) -> None:
    # pytest-socket: only the test database is reachable.
    host = make_url(_database_url()).host or "localhost"
    config.option.allow_hosts = f"{host},localhost,127.0.0.1,::1"


@pytest.fixture(scope="session")
def database_url(worker_id: str) -> str:
    """One database per pytest-xdist worker, created from scratch for each run."""
    url = make_url(_database_url())
    if worker_id == "master":
        return url.render_as_string(hide_password=False)
    worker_url = url.set(database=f"{url.database}_{worker_id}")
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        name = conn.dialect.identifier_preparer.quote(worker_url.database or "")
        conn.execute(text(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)"))
        conn.execute(text(f"CREATE DATABASE {name}"))
    admin.dispose()
    return worker_url.render_as_string(hide_password=False)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch, database_url: str) -> None:
    monkeypatch.setenv("TEST_DATABASE_URL", database_url)
    monkeypatch.delenv("HOURPLAN_OVERLAY_DIR", raising=False)


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    url = database_url
    cfg = alembic_config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture
def app(engine: Engine) -> Iterator[Flask]:
    app = create_app("testing")
    connection = engine.connect()
    outer = connection.begin()
    db.session_factory = sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False
    )
    limiter.reset()
    yield app
    outer.rollback()
    connection.close()


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


@pytest.fixture
def session(app: Flask) -> Iterator[Session]:
    with app.app_context():
        yield db.session


@pytest.fixture
def make_user(session: Session) -> Callable[..., User]:
    counter = iter(range(1, 10_000))

    def _make(*, email: str | None = None, active: bool = True, password: str = PASSWORD) -> User:
        n = next(counter)
        user = services.create_user(
            session,
            email=email or f"user{n}@example.test",
            display_name=f"Usuario {n}",
            password=password,
        )
        if not active:
            user.is_active = False
            session.commit()
        return user

    return _make


def csrf_token(client: FlaskClient, path: str = "/login") -> str:
    match = CSRF_RE.search(client.get(path).get_data(as_text=True))
    assert match, "CSRF token not found"
    return match.group(1)


@pytest.fixture
def login(client: FlaskClient) -> Callable[..., TestResponse]:
    def _login(email: str, password: str = PASSWORD, next_url: str | None = None) -> TestResponse:
        path = "/login" if next_url is None else f"/login?next={next_url}"
        return client.post(
            path,
            data={"email": email, "password": password, "csrf_token": csrf_token(client)},
        )

    return _login
