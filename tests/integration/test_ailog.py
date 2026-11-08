from collections.abc import Callable
from decimal import Decimal
from io import BytesIO

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.ailog import parsers, services
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.timesheet.models import EntrySource, TimeEntry
from tests.conftest import csrf_token

CSV = (
    "fecha,codigo,minutos,descripcion\n"
    "2026-11-02,PJ-00001,50,Revisión de pruebas\n"
    "2026-11-02,PJ-99999,30,Código que no existe\n"
)
MARKDOWN = """
# Bitácora

| fecha | codigo | minutos | descripcion |
| --- | --- | --- | --- |
| 2026-11-03 | PJ-00001 | 90 | Diseño del módulo |
"""
ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\nDTSTART:20261104T090000\r\nDTEND:20261104T100000\r\n"
    "SUMMARY:Seguimiento semanal\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART;VALUE=DATE:20261105\r\nDTEND;VALUE=DATE:20261106\r\n"
    "SUMMARY:Feriado\r\nEND:VEVENT\r\n"
    "BEGIN:VEVENT\r\nDTSTART:20261106T100000\r\nDTEND:20261106T110000\r\n"
    "STATUS:CANCELLED\r\nSUMMARY:Cancelada\r\nEND:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_csv_minutes_become_quarter_hours() -> None:
    rows = parsers.parse_csv(CSV)
    assert [r.hours for r in rows] == [Decimal("0.75"), Decimal("0.5")]
    assert rows[0].code == "PJ-00001"


def test_csv_accepts_semicolons_and_hours() -> None:
    rows = parsers.parse_csv("fecha;proyecto;horas;tarea\n2026-11-02;TK-00001;1,5;Soporte\n")
    assert rows[0].hours == Decimal("1.5")


def test_markdown_table() -> None:
    assert parsers.parse_markdown(MARKDOWN)[0].hours == Decimal("1.5")


@pytest.mark.parametrize(
    "text",
    ["", "a,b\n1,2\n", "fecha,codigo,minutos\n02/11/2026,PJ-00001,30\n"],
)
def test_bad_csv_is_explained(text: str) -> None:
    with pytest.raises(parsers.ParseError):
        parsers.parse_csv(text)


def test_ics_keeps_only_timed_live_meetings() -> None:
    meetings = parsers.parse_ics(ICS)
    assert [(m.title, m.hours) for m in meetings] == [("Seguimiento semanal", Decimal("1"))]


@pytest.fixture
def me(
    session: Session, make_user: Callable[..., User], login: Callable[..., TestResponse]
) -> Resource:
    user = make_user()
    auth.grant_role(session, user, Role.DEVELOPER, actor_id=None)
    item = Resource(display_name="Ana", user_id=user.id)
    session.add(item)
    session.add(Project(code="PJ-00001", title="Portal de clientes"))
    session.add(CatalogItem(kind=CatalogKind.ACTIVITY_BUCKET.value, code="MEET", name="Reunión"))
    session.commit()
    assert login(user.email).status_code == 302
    return item


def read(client, kind: str, name: str, body: str):  # type: ignore[no-untyped-def]
    return client.post(
        "/bitacora/leer",
        data={
            "csrf_token": csrf_token(client, "/bitacora"),
            "tipo": kind,
            "archivo": (BytesIO(body.encode()), name),
        },
        content_type="multipart/form-data",
    )


def test_log_draft_marks_unknown_codes_and_saves_confirmed_lines(
    client,
    me: Resource,
    session: Session,  # type: ignore[no-untyped-def]
) -> None:
    project = session.scalars(select(Project)).one()
    page = read(client, "bitacora", "log.csv", CSV)
    html = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "No se encontró el código «PJ-99999»" in html
    assert f'value="p:{project.id}" selected' in html

    done = client.post(
        "/bitacora/confirmar",
        data={
            "csrf_token": csrf_token(client, "/bitacora"),
            "tipo": "bitacora",
            "n": "1",
            "l0-usar": "1",
            "l0-fecha": "2026-11-02",
            "l0-horas": "0.75",
            "l0-destino": f"p:{project.id}",
            "l0-desc": "Revisión de pruebas",
            "l0-ia": "1",
        },
    )
    assert done.status_code == 302
    entry = session.scalars(select(TimeEntry)).one()
    assert (entry.source, entry.ai_assisted) == (EntrySource.AI_LOG, True)

    again = read(client, "bitacora", "log.csv", CSV).get_data(as_text=True)
    assert "Parece ya guardada antes" in again


def test_calendar_goes_to_the_meeting_bucket(
    client,
    me: Resource,
    session: Session,  # type: ignore[no-untyped-def]
) -> None:
    bucket = session.scalars(select(CatalogItem)).one()
    html = read(client, "calendario", "agenda.ics", ICS).get_data(as_text=True)
    assert "Seguimiento semanal" in html
    assert f'value="b:{bucket.id}" selected' in html
    assert "Feriado" not in html


def test_bad_file_and_wrong_line_save_nothing(
    client,
    me: Resource,
    session: Session,  # type: ignore[no-untyped-def]
) -> None:
    assert read(client, "bitacora", "log.csv", "x\n").status_code == 400
    bad = client.post(
        "/bitacora/confirmar",
        data={
            "csrf_token": csrf_token(client, "/bitacora"),
            "tipo": "bitacora",
            "n": "1",
            "l0-usar": "1",
            "l0-fecha": "2026-11-02",
            "l0-horas": "0.75",
        },
    )
    assert bad.status_code == 400
    assert "Línea 1" in bad.get_data(as_text=True)
    assert session.scalars(select(TimeEntry)).first() is None


def test_build_entries_needs_a_chosen_line() -> None:
    with pytest.raises(services.AilogError):
        services.build_entries([])


def test_observer_has_no_access(
    client,
    session: Session,
    make_user: Callable[..., User],
    login: Callable[..., TestResponse],  # type: ignore[no-untyped-def]
) -> None:
    user = make_user()
    auth.grant_role(session, user, Role.OBSERVER, actor_id=None)
    session.add(Resource(display_name="Ana", user_id=user.id))
    session.commit()
    login(user.email)
    assert client.get("/bitacora").status_code == 403
