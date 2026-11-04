from collections.abc import Callable

import pytest
from flask import Flask
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

from hourplan.auth.models import User
from tests.conftest import csrf_token

Login = Callable[..., TestResponse]
MakeUser = Callable[..., User]


def test_login_page_uses_local_assets(client: FlaskClient) -> None:
    response = client.get("/login")

    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "/static/vendor/htmx.min.js" in html
    assert "/static/vendor/alpine-csp.min.js" in html
    assert "/static/css/app.css" in html
    assert "https://" not in html


def test_protected_page_redirects_anonymous_user(client: FlaskClient) -> None:
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_login_with_valid_credentials(
    client: FlaskClient, make_user: MakeUser, login: Login
) -> None:
    user = make_user(email="ana@example.test")

    response = login("ANA@example.test ")

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert user.display_name in client.get("/").get_data(as_text=True)


def test_login_records_last_login(make_user: MakeUser, login: Login) -> None:
    user = make_user()

    login(user.email)

    assert user.last_login_at is not None


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("ana@example.test", "wrong password!"),
        ("nobody@example.test", "correct horse battery staple"),
    ],
    ids=["wrong-password", "unknown-email"],
)
def test_login_rejects_bad_credentials(
    make_user: MakeUser, login: Login, email: str, password: str
) -> None:
    make_user(email="ana@example.test")

    response = login(email, password)

    assert response.status_code == 401
    assert "Correo o contraseña incorrectos" in response.get_data(as_text=True)


def test_login_rejects_inactive_user(make_user: MakeUser, login: Login) -> None:
    user = make_user(active=False)

    assert login(user.email).status_code == 401


def test_login_requires_fields(client: FlaskClient) -> None:
    response = client.post("/login", data={"csrf_token": csrf_token(client)})

    assert response.status_code == 422
    assert 'aria-invalid="true"' in response.get_data(as_text=True)


def test_login_without_csrf_token_is_rejected(client: FlaskClient, make_user: MakeUser) -> None:
    user = make_user()

    response = client.post("/login", data={"email": user.email, "password": "x"})

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("next_url", "expected"),
    [
        ("/reports", "/reports"),
        ("https://evil.example/", "/"),
        ("//evil.example/", "/"),
        ("/\\evil.example", "/"),
    ],
    ids=["relative", "absolute", "protocol-relative", "backslash"],
)
def test_login_only_redirects_inside_the_site(
    make_user: MakeUser, login: Login, next_url: str, expected: str
) -> None:
    user = make_user()

    response = login(user.email, next_url=next_url)

    assert response.headers["Location"] == expected


def test_logout_requires_post_and_ends_session(
    client: FlaskClient, make_user: MakeUser, login: Login
) -> None:
    user = make_user()
    login(user.email)

    assert client.get("/logout").status_code == 405
    response = client.post("/logout", data={"csrf_token": csrf_token(client, "/")})

    assert response.status_code == 302
    assert client.get("/").status_code == 302


def test_login_is_rate_limited(app: Flask, make_user: MakeUser, login: Login) -> None:
    user = make_user()
    limit = int(app.config["LOGIN_RATE_LIMIT"].split()[0])

    statuses = [login(user.email, "wrong password!").status_code for _ in range(limit + 1)]

    assert statuses[:limit] == [401] * limit
    assert statuses[-1] == 429
