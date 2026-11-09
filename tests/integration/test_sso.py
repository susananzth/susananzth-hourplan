import time
from collections.abc import Callable
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from hourplan.auth import oidc
from hourplan.auth.models import User

ISSUER = "https://idp.example.org"
CONFIG: dict[str, Any] = {
    "OIDC_ISSUER": ISSUER,
    "OIDC_CLIENT_ID": "hourplan",
    "OIDC_CLIENT_SECRET": "s3cret",
}


def _claims(**over: Any) -> dict[str, Any]:
    base = {
        "iss": ISSUER,
        "aud": "hourplan",
        "exp": time.time() + 60,
        "nonce": "n1",
        "email": "ana@example.org",
    }
    return {**base, **over}


def test_validate_claims_accepts_good_token() -> None:
    assert oidc.validate_claims(_claims(), CONFIG, "n1") == "ana@example.org"


@pytest.mark.parametrize(
    "over",
    [
        {"iss": "https://evil.example"},
        {"aud": "otra"},
        {"aud": ["hourplan", "otra"], "azp": "otra"},
        {"exp": time.time() - 5},
        {"nonce": "x"},
        {"email": None},
        {"email_verified": False},
    ],
)
def test_validate_claims_rejects_bad_tokens(over: dict[str, Any]) -> None:
    with pytest.raises(oidc.OidcError):
        oidc.validate_claims(_claims(**over), CONFIG, "n1")


def test_sso_is_hidden_when_not_configured(client: FlaskClient) -> None:
    assert client.get("/login/sso").status_code == 404
    assert b"cuenta de la organizaci" not in client.get("/login").data


@pytest.fixture
def sso(app: Flask, monkeypatch: pytest.MonkeyPatch) -> None:
    app.config.update(CONFIG)
    monkeypatch.setattr(
        oidc,
        "discover",
        lambda issuer: {
            "authorization_endpoint": f"{ISSUER}/auth",
            "token_endpoint": f"{ISSUER}/token",
        },
    )


@pytest.mark.usefixtures("sso")
def test_full_flow_logs_in_existing_user(
    client: FlaskClient, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    user = make_user(email="ana@example.org")
    assert b"cuenta de la organizaci" in client.get("/login").data
    start = client.get("/login/sso")
    assert start.status_code == 302
    assert start.location.startswith(f"{ISSUER}/auth?")
    assert "code_challenge_method=S256" in start.location
    with client.session_transaction() as sess:
        state = sess["oidc"]["state"]
    monkeypatch.setattr(oidc, "email_from_code", lambda *a, **k: "ANA@example.org")
    done = client.get(f"/login/sso/callback?code=abc&state={state}")
    assert done.status_code == 302
    assert done.location == "/"
    with client.session_transaction() as sess:
        assert sess["_user_id"] == str(user.id)


@pytest.mark.usefixtures("sso")
def test_callback_rejects_wrong_state(client: FlaskClient) -> None:
    client.get("/login/sso")
    done = client.get("/login/sso/callback?code=abc&state=otro")
    assert done.location.endswith("/login")
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


@pytest.mark.usefixtures("sso")
def test_unknown_or_inactive_email_is_refused(
    client: FlaskClient, make_user: Callable[..., User], monkeypatch: pytest.MonkeyPatch
) -> None:
    make_user(email="baja@example.org", active=False)
    for email in ("nadie@example.org", "baja@example.org"):
        client.get("/login/sso")
        with client.session_transaction() as sess:
            state = sess["oidc"]["state"]
        monkeypatch.setattr(oidc, "email_from_code", lambda *a, e=email, **k: e)
        done = client.get(f"/login/sso/callback?code=abc&state={state}")
        assert done.location.endswith("/login")
        with client.session_transaction() as sess:
            assert "_user_id" not in sess
