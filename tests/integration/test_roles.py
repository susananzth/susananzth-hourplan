from collections.abc import Callable

import pytest
from flask import Blueprint, Flask
from flask.testing import FlaskClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.audit.models import AuditLog
from hourplan.auth import services
from hourplan.auth.models import User
from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role

MakeUser = Callable[..., User]


@pytest.fixture
def app_with_admin_view(app: Flask) -> Flask:
    bp = Blueprint("only_admin", __name__)

    @bp.get("/only-admin")
    @roles_required(Role.ADMIN, Role.PM)
    def only_admin() -> str:
        return "ok"

    app.register_blueprint(bp)
    return app


def audit_rows(session: Session, entity_type: str) -> list[AuditLog]:
    stmt = select(AuditLog).where(AuditLog.entity_type == entity_type).order_by(AuditLog.id)
    return list(session.scalars(stmt))


def test_create_user_with_roles_is_audited(session: Session) -> None:
    user = services.create_user(
        session,
        email="pm@example.test",
        display_name="PM",
        password="a" * 12,
        roles=[Role.PM, Role.LEAD, Role.PM],
    )

    assert user.roles == {Role.PM, Role.LEAD}
    assert user.has_role(Role.ADMIN, Role.LEAD)
    assert not user.has_role(Role.ADMIN)
    [row] = audit_rows(session, "user")
    assert row.changes == {"email": "pm@example.test", "roles": ["lead", "pm"]}
    assert row.subject_user_id == user.id


def test_grant_and_revoke_role(session: Session, make_user: MakeUser) -> None:
    user = make_user()
    admin = make_user()

    assert services.grant_role(session, user, Role.LEAD, actor_id=admin.id)
    assert not services.grant_role(session, user, Role.LEAD, actor_id=admin.id)
    session.expire_all()
    assert user.roles == {Role.LEAD}

    assert services.revoke_role(session, user, Role.LEAD, actor_id=admin.id)
    assert not services.revoke_role(session, user, Role.LEAD, actor_id=admin.id)
    session.expire_all()
    assert user.roles == frozenset()

    rows = audit_rows(session, "user_role")
    assert [(row.action, row.actor_id, row.changes) for row in rows] == [
        ("create", admin.id, {"role": "lead"}),
        ("delete", admin.id, {"role": "lead"}),
    ]


def test_roles_required_redirects_anonymous(app_with_admin_view: Flask) -> None:
    response = app_with_admin_view.test_client().get("/only-admin")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_roles_required_forbids_users_without_role(
    app_with_admin_view: Flask,
    client: FlaskClient,
    make_user: MakeUser,
    login: Callable[..., TestResponse],
) -> None:
    user = make_user()
    login(user.email)

    assert client.get("/only-admin").status_code == 403


def test_roles_required_allows_any_listed_role(
    app_with_admin_view: Flask,
    client: FlaskClient,
    session: Session,
    make_user: MakeUser,
    login: Callable[..., TestResponse],
) -> None:
    user = make_user()
    services.grant_role(session, user, Role.PM, actor_id=None)
    login(user.email)

    assert client.get("/only-admin").get_data(as_text=True) == "ok"


def test_roles_required_needs_a_role() -> None:
    with pytest.raises(ValueError, match="at least one role"):
        roles_required()


def test_cli_grant_and_revoke_role(app: Flask, make_user: MakeUser, session: Session) -> None:
    user = make_user()
    runner = app.test_cli_runner()

    assert "Rol asignado" in runner.invoke(args=["grant-role", user.email, "lead"]).output
    assert "ya tenía" in runner.invoke(args=["grant-role", user.email, "lead"]).output
    assert "Rol retirado" in runner.invoke(args=["revoke-role", user.email, "lead"]).output
    assert "no tenía" in runner.invoke(args=["revoke-role", user.email, "lead"]).output
    result = runner.invoke(args=["grant-role", "nadie@example.test", "lead"])
    assert result.exit_code != 0
    assert "No existe" in result.output


def test_cli_create_user_with_role(app: Flask, session: Session) -> None:
    result = app.test_cli_runner().invoke(
        args=["create-user", "--email", "a@example.test", "--name", "A", "--role", "admin"],
        input="a" * 12 + "\n" + "a" * 12 + "\n",
    )

    assert result.exit_code == 0, result.output
    user = services.find_by_email(session, "a@example.test")
    assert user is not None
    assert user.roles == {Role.ADMIN}
