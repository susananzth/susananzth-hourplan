import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from flask import Flask
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.ailog import services
from hourplan.ailog.models import AccessToken, AiDraft, DraftStatus
from hourplan.audit.models import AuditLog
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.timesheet.models import EntrySource, TimeEntry
from tests.conftest import csrf_token

NOW = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)


@pytest.fixture
def mcp_on(app: Flask) -> None:
    app.config["MCP_ENABLED"] = True


@pytest.fixture
def me(
    session: Session,
    mcp_on: None,
    make_user: Callable[..., User],
    login: Callable[..., TestResponse],
) -> Resource:
    user = make_user()
    auth.grant_role(session, user, Role.DEVELOPER, actor_id=None)
    item = Resource(display_name="Ana", user_id=user.id)
    session.add(item)
    session.add(Project(code="PJ-00001", title="Portal de clientes"))
    session.commit()
    assert login(user.email).status_code == 302
    return item


@pytest.fixture
def token(session: Session, me: Resource) -> str:
    assert me.user_id is not None
    _, plain = services.create_token(
        session, me.user_id, "Portátil", days=90, now=datetime.now(UTC)
    )
    return plain


def rpc(client, token: str | None, method: str, params: dict | None = None, **headers: str):  # type: ignore[no-untyped-def]
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", json=body, headers=headers)


def call(client, token: str, name: str, args: dict) -> dict:  # type: ignore[no-untyped-def]
    reply = rpc(client, token, "tools/call", {"name": name, "arguments": args})
    assert reply.status_code == 200
    return reply.get_json()["result"]


def test_endpoint_is_off_by_default(client) -> None:  # type: ignore[no-untyped-def]
    assert client.post("/mcp", json={}).status_code == 404


def test_initialize_and_list_tools(client, token: str) -> None:  # type: ignore[no-untyped-def]
    init = rpc(client, token, "initialize", {"protocolVersion": "2025-06-18"}).get_json()
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert init["result"]["capabilities"] == {"tools": {"listChanged": False}}
    other = rpc(client, token, "initialize", {"protocolVersion": "1999-01-01"}).get_json()
    assert other["result"]["protocolVersion"] == "2025-11-25"
    names = {t["name"] for t in rpc(client, token, "tools/list").get_json()["result"]["tools"]}
    assert names == {"buscar_proyectos", "ver_borradores", "proponer_horas"}
    note = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert note.status_code == 202
    assert rpc(client, token, "nada").get_json()["error"]["code"] == -32601


def test_requests_without_a_live_token_are_refused(
    client,
    session: Session,
    token: str,  # type: ignore[no-untyped-def]
) -> None:
    assert rpc(client, None, "ping").status_code == 401
    assert rpc(client, "hp_falso", "ping").status_code == 401
    assert rpc(client, token, "ping", Origin="https://malo.test").status_code == 403
    assert rpc(client, token, "ping", **{"MCP-Protocol-Version": "1999"}).status_code == 400
    assert client.get("/mcp").status_code == 405

    stored = session.scalars(select(AccessToken)).one()
    assert token not in stored.token_hash
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    assert rpc(client, token, "ping").status_code == 401


def test_revoked_token_is_refused_and_audited(
    client,
    session: Session,
    me: Resource,
    token: str,  # type: ignore[no-untyped-def]
) -> None:
    assert rpc(client, token, "ping").status_code == 200
    stored = session.scalars(select(AccessToken)).one()
    page = client.post(
        f"/bitacora/conexion/{stored.id}/revocar",
        data={"csrf_token": csrf_token(client, "/bitacora/conexion")},
    )
    assert page.status_code == 302
    assert rpc(client, token, "ping").status_code == 401
    actions = {
        a.action
        for a in session.scalars(select(AuditLog).where(AuditLog.entity_type == "access_token"))
    }
    assert actions == {"create", "update"}


def test_tools_search_propose_and_list(client, session: Session, token: str) -> None:  # type: ignore[no-untyped-def]
    found = call(client, token, "buscar_proyectos", {"texto": "portal"})
    assert found["structuredContent"]["proyectos"][0]["codigo"] == "PJ-00001"

    bad = call(
        client,
        token,
        "proponer_horas",
        {"lineas": [{"fecha": "ayer", "codigo": "X", "minutos": 5}]},
    )
    assert bad["isError"] is True
    assert session.scalars(select(AiDraft)).first() is None

    ok = call(
        client,
        token,
        "proponer_horas",
        {
            "lineas": [
                {
                    "fecha": "2026-11-02",
                    "codigo": "PJ-00001",
                    "minutos": 50,
                    "descripcion": "Refactor del módulo",
                }
            ]
        },
    )
    assert ok["structuredContent"]["guardadas"] == 1
    assert ok["structuredContent"]["revisar_en"].endswith("/bitacora/pendientes")
    draft = session.scalars(select(AiDraft)).one()
    assert draft.hours == Decimal("0.75")
    listed = call(client, token, "ver_borradores", {})
    assert listed["structuredContent"]["borradores"][0]["codigo"] == "PJ-00001"
    assert session.scalars(select(TimeEntry)).first() is None


def test_person_confirms_one_and_discards_the_other(
    client,
    session: Session,
    token: str,  # type: ignore[no-untyped-def]
) -> None:
    call(
        client,
        token,
        "proponer_horas",
        {
            "lineas": [
                {"fecha": "2026-11-02", "codigo": "PJ-00001", "minutos": 60},
                {"fecha": "2026-11-02", "codigo": "PJ-99999", "minutos": 30},
            ]
        },
    )
    assert "La IA propuso 2 líneas" in client.get("/bitacora").get_data(as_text=True)
    html = client.get("/bitacora/pendientes").get_data(as_text=True)
    assert "No se encontró el código «PJ-99999»" in html
    project = session.scalars(select(Project)).one()
    first, second = session.scalars(select(AiDraft).order_by(AiDraft.id)).all()
    done = client.post(
        "/bitacora/confirmar",
        data={
            "csrf_token": csrf_token(client, "/bitacora"),
            "tipo": "ia",
            "n": "2",
            "l0-usar": "1",
            "l0-fecha": "2026-11-02",
            "l0-horas": "1",
            "l0-destino": f"p:{project.id}",
            "l0-ia": "1",
            "l0-borrador": str(first.id),
            "l1-fecha": "2026-11-02",
            "l1-horas": "0.5",
            "l1-borrador": str(second.id),
        },
    )
    assert done.status_code == 302
    session.expire_all()
    assert (first.status, second.status) == (DraftStatus.CONFIRMED, DraftStatus.DISCARDED)
    entry = session.scalars(select(TimeEntry)).one()
    assert (entry.source, entry.ai_assisted) == (EntrySource.AI_LOG, True)


def test_token_page_shows_the_token_once_and_limits_count(
    client,
    session: Session,
    me: Resource,  # type: ignore[no-untyped-def]
) -> None:
    page = client.post(
        "/bitacora/conexion",
        data={"csrf_token": csrf_token(client, "/bitacora/conexion"), "nombre": "Portátil"},
    )
    html = page.get_data(as_text=True)
    assert "claude mcp add --transport http hourplan" in html
    plain = re.search(r'value="(hp_[^"]+)"', html)
    assert plain is not None
    assert plain.group(1) not in client.get("/bitacora/conexion").get_data(as_text=True)
    assert me.user_id is not None
    for n in range(services.MAX_TOKENS - 1):
        services.create_token(session, me.user_id, f"t{n}", days=90, now=NOW)
    with pytest.raises(services.AilogError):
        services.create_token(session, me.user_id, "uno más", days=90, now=NOW)


def test_drafts_are_private_to_their_owner(
    client,
    session: Session,
    token: str,
    make_user,  # type: ignore[no-untyped-def]
) -> None:
    call(
        client,
        token,
        "proponer_horas",
        {"lineas": [{"fecha": "2026-11-02", "codigo": "PJ-00001", "minutos": 60}]},
    )
    other = make_user()
    auth.grant_role(session, other, Role.DEVELOPER, actor_id=None)
    resource = Resource(display_name="Beto", user_id=other.id)
    session.add(resource)
    session.commit()
    assert services.pending_drafts(session, resource.id) == []
    assert services.discard_pending(session, resource.id) == 0
