from collections.abc import Callable

from flask.testing import FlaskClient
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.auth.models import User
from hourplan.auth.roles import Role
from tests.integration.test_admin import sign_in

MakeUser = Callable[..., User]
Login = Callable[..., TestResponse]


def test_admin_sees_every_group_in_the_side_menu(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.ADMIN)
    html = client.get("/admin/usuarios").get_data(as_text=True)
    assert 'id="hp-sidebar"' in html
    assert 'id="hp-nav-toggle"' in html
    for group in ("Mi trabajo", "Proyectos", "Catálogos", "Administración"):
        assert f'<span class="flex-1">{group}</span>' in html
    # The group of the current page is open and its link marked.
    assert 'href="/admin/usuarios" class="flex items-center gap-3 rounded-md py-2 pl-5 pr-3' in html
    assert 'aria-current="page"><svg' in html
    assert "vendor/icons.svg#users" in html


def test_developer_has_no_admin_groups(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.DEVELOPER)
    html = client.get("/").get_data(as_text=True)
    assert "Mi trabajo" in html
    assert "/admin/usuarios" not in html
    assert '<span class="flex-1">Administración</span>' not in html


def test_side_menu_follows_the_language(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.ADMIN)
    client.set_cookie("lang", "en")
    html = client.get("/").get_data(as_text=True)
    assert '<span class="flex-1">My work</span>' in html
    assert "Show or hide the menu" in html


def test_login_page_has_no_side_menu(client: FlaskClient) -> None:
    assert 'id="hp-sidebar"' not in client.get("/login").get_data(as_text=True)
