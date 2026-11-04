from collections.abc import Callable

import pytest
from flask import Flask
from sqlalchemy.orm import Session

from hourplan.auth import services
from hourplan.auth.models import User

MakeUser = Callable[..., User]


def test_create_user_hashes_password_and_normalizes_email(session: Session) -> None:
    user = services.create_user(
        session, email="  Ana@Example.TEST ", display_name=" Ana ", password="a" * 12
    )

    assert user.id is not None
    assert user.email == "ana@example.test"
    assert user.display_name == "Ana"
    assert user.password_hash.startswith("$argon2")


def test_create_user_rejects_duplicate_email_ignoring_case(
    session: Session, make_user: MakeUser
) -> None:
    make_user(email="ana@example.test")

    with pytest.raises(services.EmailAlreadyRegisteredError):
        services.create_user(
            session, email="ANA@example.test", display_name="Otra", password="a" * 12
        )


def test_create_user_rejects_short_password(session: Session) -> None:
    with pytest.raises(services.WeakPasswordError, match="12"):
        services.create_user(session, email="a@example.test", display_name="A", password="short")


def test_authenticate_returns_none_for_corrupt_hash(session: Session, make_user: MakeUser) -> None:
    user = make_user()
    user.password_hash = "not-a-hash"
    session.commit()

    assert services.authenticate(session, email=user.email, password="whatever") is None


def test_get_active_user_ignores_inactive(session: Session, make_user: MakeUser) -> None:
    user = make_user(active=False)

    assert services.get_active_user(session, user.id) is None


def test_create_user_command(app: Flask, session: Session) -> None:
    result = app.test_cli_runner().invoke(
        args=["create-user", "--email", "cli@example.test", "--name", "Cli", "--password", "b" * 12]
    )

    assert result.exit_code == 0, result.output
    assert services.find_by_email(session, "cli@example.test") is not None


def test_create_user_command_reports_errors(app: Flask) -> None:
    result = app.test_cli_runner().invoke(
        args=["create-user", "--email", "x@example.test", "--name", "X", "--password", "short"]
    )

    assert result.exit_code != 0
    assert "at least 12" in result.output
