from flask.testing import FlaskClient

from tests.conftest import csrf_token


def test_default_is_spanish_and_accept_language_picks_english(client: FlaskClient) -> None:
    assert b"Iniciar sesi" in client.get("/login").data
    page = client.get("/login", headers={"Accept-Language": "en-US,en;q=0.9"}).data
    assert b"Sign in" in page
    assert b'<html lang="en"' in page


def test_language_switch_sets_cookie_and_is_remembered(client: FlaskClient) -> None:
    token = csrf_token(client)
    response = client.post("/idioma", data={"lang": "en", "next": "/login", "csrf_token": token})
    assert response.location == "/login"
    assert "lang=en" in response.headers["Set-Cookie"]
    assert b"Sign in" in client.get("/login").data
    token = csrf_token(client)
    client.post("/idioma", data={"lang": "es", "next": "/login", "csrf_token": token})
    assert b"Iniciar sesi" in client.get("/login").data


def test_language_switch_ignores_unknown_language_and_foreign_redirect(
    client: FlaskClient,
) -> None:
    token = csrf_token(client)
    response = client.post(
        "/idioma", data={"lang": "xx", "next": "https://evil.example", "csrf_token": token}
    )
    assert response.location == "/"
    assert "Set-Cookie" not in response.headers


def test_login_failure_message_is_translated(client: FlaskClient) -> None:
    client.set_cookie("lang", "en")
    token = csrf_token(client)
    page = client.post(
        "/login", data={"email": "x@example.org", "password": "bad", "csrf_token": token}
    )
    assert b"Incorrect email or password." in page.data
