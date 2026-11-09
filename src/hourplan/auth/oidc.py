"""Optional single sign-on with OpenID Connect (authorization code flow with PKCE).

The ID token comes straight from the provider's token endpoint over TLS, so (as OIDC Core
3.1.3.7 allows) its signature is not re-checked; issuer, audience, expiry and nonce are.
People are never created from SSO: the provider's email must match an active local user.
"""

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlencode

import httpx

TIMEOUT = 10.0
_discovery_cache: dict[str, dict[str, Any]] = {}


class OidcError(Exception):
    """The provider's answer cannot be trusted or the flow was tampered with."""


def is_enabled(config: Mapping[str, Any]) -> bool:
    return all(config.get(key) for key in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"))


def discover(issuer: str) -> dict[str, Any]:
    issuer = issuer.rstrip("/")
    if issuer not in _discovery_cache:
        try:
            response = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=TIMEOUT)
            response.raise_for_status()
            doc = response.json()
        except (httpx.HTTPError, ValueError) as err:
            raise OidcError("discovery failed") from err
        if not isinstance(doc, dict) or str(doc.get("issuer", "")).rstrip("/") != issuer:
            raise OidcError("issuer mismatch")
        if not (doc.get("authorization_endpoint") and doc.get("token_endpoint")):
            raise OidcError("incomplete discovery document")
        _discovery_cache[issuer] = doc
    return _discovery_cache[issuer]


def new_flow() -> dict[str, str]:
    """Random values to keep in the session between the redirect and the callback."""
    return {
        "state": secrets.token_urlsafe(32),
        "nonce": secrets.token_urlsafe(32),
        "verifier": secrets.token_urlsafe(48),
    }


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(config: Mapping[str, Any], flow: Mapping[str, str], redirect_uri: str) -> str:
    doc = discover(str(config["OIDC_ISSUER"]))
    query = urlencode(
        {
            "response_type": "code",
            "client_id": config["OIDC_CLIENT_ID"],
            "redirect_uri": redirect_uri,
            "scope": config.get("OIDC_SCOPES") or "openid email profile",
            "state": flow["state"],
            "nonce": flow["nonce"],
            "code_challenge": _challenge(flow["verifier"]),
            "code_challenge_method": "S256",
        }
    )
    return f"{doc['authorization_endpoint']}?{query}"


def _claims(id_token: str) -> dict[str, Any]:
    try:
        payload = id_token.split(".")[1]
        data = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        claims = json.loads(data)
    except (IndexError, ValueError) as err:
        raise OidcError("malformed id_token") from err
    if not isinstance(claims, dict):
        raise OidcError("malformed id_token")
    return claims


def validate_claims(claims: Mapping[str, Any], config: Mapping[str, Any], nonce: str) -> str:
    """Check the ID token claims and return the verified email address."""
    client_id = str(config["OIDC_CLIENT_ID"])
    if str(claims.get("iss", "")).rstrip("/") != str(config["OIDC_ISSUER"]).rstrip("/"):
        raise OidcError("wrong issuer")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences or (len(audiences) > 1 and claims.get("azp") != client_id):
        raise OidcError("wrong audience")
    expires = claims.get("exp")
    if not isinstance(expires, int | float) or expires < time.time():
        raise OidcError("expired token")
    if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OidcError("wrong nonce")
    email = claims.get("email")
    if not isinstance(email, str) or not email or claims.get("email_verified") is False:
        raise OidcError("no verified email")
    return email


def email_from_code(
    config: Mapping[str, Any], flow: Mapping[str, str], code: str, redirect_uri: str
) -> str:
    """Swap the authorization code for tokens and return the verified email."""
    doc = discover(str(config["OIDC_ISSUER"]))
    try:
        response = httpx.post(
            doc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": config["OIDC_CLIENT_ID"],
                "client_secret": config["OIDC_CLIENT_SECRET"],
                "code_verifier": flow["verifier"],
            },
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        id_token = response.json().get("id_token")
    except (httpx.HTTPError, ValueError, AttributeError) as err:
        raise OidcError("token exchange failed") from err
    if not isinstance(id_token, str):
        raise OidcError("no id_token")
    return validate_claims(_claims(id_token), config, flow["nonce"])
