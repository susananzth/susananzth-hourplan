from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.audit.models import AuditLog
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs import services as catalogs
from hourplan.catalogs.models import Application, CatalogItem, CatalogKind, Portfolio, Repository
from hourplan.policies.models import WorkPolicy
from tests.conftest import csrf_token

MakeUser = Callable[..., User]
Login = Callable[..., TestResponse]

ADMIN_PAGES = ["/admin/usuarios", "/admin/usuarios/nuevo", "/admin/politicas"]
CATALOG_PAGES = [
    "/admin/catalogos",
    "/admin/catalogos/nuevo?kind=priority",
    "/admin/carteras",
    "/admin/carteras/nueva",
    "/admin/aplicaciones",
    "/admin/aplicaciones/nueva",
    "/admin/repositorios",
    "/admin/repositorios/nuevo",
]


def sign_in(session: Session, make_user: MakeUser, login: Login, *roles: Role) -> User:
    user = make_user()
    for role in roles:
        auth.grant_role(session, user, role, actor_id=None)
    assert login(user.email).status_code == 302
    return user


def post(client: FlaskClient, path: str, data: dict[str, object], page: str | None = None):
    token = csrf_token(client, page or path)
    return client.post(path, data={**data, "csrf_token": token})


@pytest.mark.parametrize("path", ADMIN_PAGES + CATALOG_PAGES)
def test_anonymous_is_sent_to_login(client: FlaskClient, path: str) -> None:
    assert client.get(path).status_code == 302


@pytest.mark.parametrize("role", [Role.DEVELOPER, Role.LEAD, Role.OBSERVER])
@pytest.mark.parametrize("path", ADMIN_PAGES + CATALOG_PAGES)
def test_other_roles_get_403(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login, role: Role, path: str
) -> None:
    sign_in(session, make_user, login, role)
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("path", ADMIN_PAGES)
def test_pm_cannot_open_admin_only_pages(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login, path: str
) -> None:
    sign_in(session, make_user, login, Role.PM)
    assert client.get(path).status_code == 403


@pytest.mark.parametrize("path", CATALOG_PAGES)
def test_pm_opens_catalog_pages(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login, path: str
) -> None:
    sign_in(session, make_user, login, Role.PM)
    assert client.get(path).status_code == 200


def test_admin_opens_everything_and_nav_follows_role(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.ADMIN)
    for path in ADMIN_PAGES + CATALOG_PAGES:
        assert client.get(path).status_code == 200, path
    html = client.get("/").get_data(as_text=True)
    assert "/admin/usuarios" in html
    assert "/admin/catalogos" in html


def test_nav_hidden_for_roles_without_admin_access(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.DEVELOPER)
    assert "/admin/" not in client.get("/").get_data(as_text=True)


def test_pm_nav_has_catalogs_but_not_users(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.PM)
    html = client.get("/").get_data(as_text=True)
    assert "/admin/catalogos" in html
    assert "/admin/usuarios" not in html


def test_catalog_item_flow(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    admin = sign_in(session, make_user, login, Role.PM)
    response = post(
        client,
        "/admin/catalogos/nuevo?kind=project_status",
        {"code": "DONE", "name": "Cerrado", "sort_order": 1, "is_closed": "y"},
    )
    assert response.status_code == 302
    item = session.scalars(select(CatalogItem).where(CatalogItem.code == "DONE")).one()
    assert item.attributes == {"is_closed": True}

    duplicate = post(
        client, "/admin/catalogos/nuevo?kind=project_status", {"code": "DONE", "name": "Otro"}
    )
    assert duplicate.status_code == 200
    assert "Ya existe" in duplicate.get_data(as_text=True)

    path = f"/admin/catalogos/{item.id}"
    assert post(client, path, {"name": "Cerrado ok", "sort_order": 2}).status_code == 302
    session.refresh(item)
    assert (item.name, item.attributes) == ("Cerrado ok", {"is_closed": False})

    assert post(client, f"{path}/activo", {}, "/admin/catalogos").status_code == 302
    session.refresh(item)
    assert item.is_active is False
    actors = {
        a.actor_id for a in session.scalars(select(AuditLog).where(AuditLog.entity_id == item.id))
    }
    assert actors == {admin.id}
    assert client.get("/admin/catalogos/999999").status_code == 404
    assert client.get("/admin/catalogos?kind=nope").status_code == 404


def test_hierarchical_item_form_lists_parents(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.ADMIN)
    category = catalogs.create_item(
        session, kind=CatalogKind.REWORK_CATEGORY, code="C", name="Categoria uno", actor_id=None
    )
    page = client.get("/admin/catalogos/nuevo?kind=rework_reason").get_data(as_text=True)
    assert "Categoria uno" in page
    response = post(
        client,
        "/admin/catalogos/nuevo?kind=rework_reason",
        {"code": "R", "name": "Motivo", "parent_id": category.id},
    )
    assert response.status_code == 302
    edit = session.scalars(select(CatalogItem).where(CatalogItem.code == "R")).one()
    assert edit.parent_id == category.id
    assert client.get(f"/admin/catalogos/{edit.id}").status_code == 200


def test_portfolio_application_repository_screens(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.ADMIN)
    assert (
        post(client, "/admin/carteras/nueva", {"code": "P", "name": "Cartera"}).status_code == 302
    )
    portfolio = session.scalars(select(Portfolio)).one()
    assert (
        post(client, f"/admin/carteras/{portfolio.id}", {"name": "N", "sort_order": 1}).status_code
        == 302
    )
    assert (
        post(client, f"/admin/carteras/{portfolio.id}/activo", {}, "/admin/carteras").status_code
        == 302
    )

    app_type = catalogs.create_item(
        session, kind=CatalogKind.APPLICATION_TYPE, code="WEB", name="Web", actor_id=None
    )
    assert (
        post(
            client,
            "/admin/aplicaciones/nueva",
            {"code": "A", "name": "App", "type_id": app_type.id},
        ).status_code
        == 302
    )
    application = session.scalars(select(Application)).one()
    assert application.type_id == app_type.id
    assert (
        post(
            client, f"/admin/aplicaciones/{application.id}", {"name": "App2", "type_id": 0}
        ).status_code
        == 302
    )
    assert (
        post(
            client, f"/admin/aplicaciones/{application.id}/activo", {}, "/admin/aplicaciones"
        ).status_code
        == 302
    )

    assert (
        post(
            client,
            "/admin/repositorios/nuevo",
            {"name": "repo", "url": "https://git.test/r", "application_id": application.id},
        ).status_code
        == 302
    )
    repo = session.scalars(select(Repository)).one()
    bad_url = post(
        client, f"/admin/repositorios/{repo.id}", {"name": "repo", "url": "no", "application_id": 0}
    )
    assert bad_url.status_code == 200
    assert (
        post(
            client,
            f"/admin/repositorios/{repo.id}",
            {"name": "repo", "url": "", "application_id": 0},
        ).status_code
        == 302
    )
    assert (
        post(client, f"/admin/repositorios/{repo.id}/activo", {}, "/admin/repositorios").status_code
        == 302
    )
    for path in ("/admin/carteras", "/admin/aplicaciones", "/admin/repositorios"):
        assert client.get(path).status_code == 200
        assert client.get(f"{path}/999999").status_code == 404


def test_user_management(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    admin = sign_in(session, make_user, login, Role.ADMIN)
    created = post(
        client,
        "/admin/usuarios/nuevo",
        {
            "email": "new@example.test",
            "display_name": "Nueva",
            "password": "a-long-enough-password",
            "role": "developer",
        },
    )
    assert created.status_code == 302
    user = auth.find_by_email(session, "new@example.test")
    assert user is not None
    assert user.roles == {Role.DEVELOPER}

    weak = post(
        client,
        "/admin/usuarios/nuevo",
        {"email": "x@example.test", "display_name": "X", "password": "short", "role": ""},
    )
    assert weak.status_code == 200
    dup = post(
        client,
        "/admin/usuarios/nuevo",
        {"email": "NEW@example.test", "display_name": "X", "password": "a-long-enough-password"},
    )
    assert "Ya existe un usuario" in dup.get_data(as_text=True)

    page = f"/admin/usuarios/{user.id}"
    assert client.get(page).status_code == 200
    assert post(client, f"{page}/roles", {"role": "lead"}, page).status_code == 302
    session.refresh(user)
    assert user.roles == {Role.DEVELOPER, Role.LEAD}
    assert post(client, f"{page}/roles/developer/quitar", {}, page).status_code == 302
    session.refresh(user)
    assert user.roles == {Role.LEAD}
    assert post(client, f"{page}/roles/nope/quitar", {}, page).status_code == 404
    assert post(client, f"{page}/activo", {}, page).status_code == 302
    session.refresh(user)
    assert user.is_active is False

    own = f"/admin/usuarios/{admin.id}"
    assert post(client, f"{own}/roles/admin/quitar", {}, own).status_code == 302
    assert post(client, f"{own}/activo", {}, own).status_code == 302
    session.refresh(admin)
    assert admin.is_active
    assert Role.ADMIN in admin.roles
    assert client.get("/admin/usuarios/999999").status_code == 404


def test_schedule_policy_from_screen(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.ADMIN)
    path = "/admin/politicas/trabajo/nueva"
    future = (datetime.now().astimezone().date() + timedelta(days=30)).isoformat()
    data = {
        "effective_from": future,
        "standard_daily_hours": "8",
        "daily_warning_hours": "10",
        "max_project_weeks": "3",
        "idle_minutes": "5",
    }
    assert post(client, path, data).status_code == 302
    assert session.scalars(select(WorkPolicy)).one().effective_from.isoformat() == future

    past = post(client, path, {**data, "effective_from": "2020-01-01"})
    assert past.status_code == 200
    assert "fecha futura" in past.get_data(as_text=True)
    inverted = post(client, path, {**data, "daily_warning_hours": "5"})
    assert inverted.status_code == 200

    evaluation = {
        "effective_from": future,
        "colors_enabled": "y",
        "hours_tolerance_pct": "10",
        "hours_red_pct": "20",
        "dates_tolerance_pct": "10",
        "dates_red_pct": "20",
    }
    assert post(client, "/admin/politicas/evaluacion/nueva", evaluation).status_code == 302
    listing = client.get("/admin/politicas").get_data(as_text=True)
    assert "Programada" in listing
    assert client.get("/admin/politicas/otra/nueva").status_code == 404


# ------------------------------------------------------------------ link a user to a resource


def test_admin_links_a_user_to_a_free_resource_and_to_a_new_one(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    from hourplan.resources.models import Resource

    sign_in(session, make_user, login, Role.ADMIN)
    free = Resource(display_name="Persona Importada")
    session.add(free)
    session.commit()
    first, second = make_user(), make_user()
    page = f"/admin/usuarios/{first.id}"
    assert "Persona Importada" in client.get(page).get_data(as_text=True)

    done = post(client, f"{page}/recurso", {"resource_id": free.id, "name": ""}, page)
    assert done.status_code == 302
    session.refresh(free)
    assert free.user_id == first.id
    assert "Vinculado al recurso" in client.get(page).get_data(as_text=True)
    listing = client.get("/admin/usuarios").get_data(as_text=True)
    assert "Persona Importada" in listing
    assert "Sin vincular" in listing

    other = f"/admin/usuarios/{second.id}"
    post(client, f"{other}/recurso", {"resource_id": 0, "name": "Nombre Nuevo"}, other)
    created = session.query(Resource).filter_by(display_name="Nombre Nuevo").one()
    assert created.user_id == second.id


def test_linking_twice_is_refused_with_a_message(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.ADMIN)
    user = make_user()
    page = f"/admin/usuarios/{user.id}"
    post(client, f"{page}/recurso", {"resource_id": 0, "name": "Una Persona"}, page)

    again = post(client, f"{page}/recurso", {"resource_id": 0, "name": "Otra"}, page)
    followed = client.get(again.headers["Location"]).get_data(as_text=True)

    assert "Vinculado al recurso" in followed
    assert "Otra" not in followed


def test_only_admin_can_link_resources(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.PM)
    user = make_user()
    token = csrf_token(client, "/admin/aplicaciones")
    denied = client.post(
        f"/admin/usuarios/{user.id}/recurso", data={"name": "X", "csrf_token": token}
    )
    assert denied.status_code == 403
