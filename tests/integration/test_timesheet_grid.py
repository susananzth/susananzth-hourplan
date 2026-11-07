from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.timesheet import services
from hourplan.timesheet.models import TimeEntry
from hourplan.timesheet.services import RowKey
from tests.conftest import csrf_token

MONDAY = date(2026, 6, 15)
WEEK = "/horas?semana=2026-06-15"


@pytest.fixture
def project(session: Session) -> Project:
    item = Project(code="PROY-0001-2026", title="Portal")
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def me(
    session: Session, make_user: Callable[..., User], login: Callable[..., TestResponse]
) -> Resource:
    user = make_user()
    auth.grant_role(session, user, Role.DEVELOPER, actor_id=None)
    resource = Resource(display_name="Ana", user_id=user.id)
    session.add(resource)
    session.commit()
    assert login(user.email).status_code == 302
    return resource


def hours_of(session: Session, resource: Resource) -> list[Decimal]:
    return sorted(
        session.scalars(select(TimeEntry.hours).where(TimeEntry.resource_id == resource.id))
    )


def post_cell(
    client: FlaskClient, project: Project, day: str, hours: str, extra: list[str] | None = None
) -> TestResponse:
    return client.post(
        "/horas/celda",
        data={
            "fila": f"{project.id},,,",
            "dia": day,
            "horas": hours,
            "semana": "2026-06-15",
            "extra": extra or [],
            "csrf_token": csrf_token(client, WEEK),
        },
        headers={"HX-Request": "true"},
    )


def test_set_cell_creates_changes_and_clears(
    session: Session, me: Resource, project: Project
) -> None:
    key = RowKey(project_id=project.id)

    services.set_cell(session, me.id, key, MONDAY, Decimal(4), actor_id=None)
    services.set_cell(session, me.id, key, MONDAY, Decimal("5.5"), actor_id=None)
    assert hours_of(session, me) == [Decimal("5.5")]

    services.set_cell(session, me.id, key, MONDAY, Decimal(0), actor_id=None)
    assert hours_of(session, me) == []


def test_cell_with_several_entries_is_not_editable(
    session: Session, me: Resource, project: Project
) -> None:
    for hours in ("1", "2"):
        services.create_entry(
            session,
            me.id,
            services.EntryData(work_date=MONDAY, hours=Decimal(hours), project_id=project.id),
            actor_id=None,
        )
    grid = services.week_grid(session, me, MONDAY)

    assert grid.rows[0].cells[0].multiple
    assert grid.rows[0].cells[0].hours == Decimal(3)
    with pytest.raises(services.EntryError):
        services.set_cell(
            session, me.id, RowKey(project_id=project.id), MONDAY, Decimal(1), actor_id=None
        )


def test_search_finds_projects_by_code_and_buckets_by_name(
    session: Session, project: Project
) -> None:
    session.add(CatalogItem(kind=CatalogKind.ACTIVITY_BUCKET.value, code="m", name="Reunión"))
    session.commit()

    assert [t.key.project_id for t in services.search_targets(session, "portal")] == [project.id]
    assert services.search_targets(session, "reuni")[0].label == "Reunión"
    assert services.search_targets(session, "  ") == []


def test_anonymous_is_sent_to_login(client: FlaskClient) -> None:
    assert client.get("/horas").status_code == 302


def test_user_without_resource_sees_an_explanation(
    session: Session,
    make_user: Callable[..., User],
    login: Callable[..., TestResponse],
    client: FlaskClient,
) -> None:
    user = make_user()
    auth.grant_role(session, user, Role.DEVELOPER, actor_id=None)
    login(user.email)

    assert "no está vinculado" in client.get("/horas").get_data(as_text=True)


def test_observer_cannot_open_the_grid(
    session: Session,
    make_user: Callable[..., User],
    login: Callable[..., TestResponse],
    client: FlaskClient,
) -> None:
    user = make_user()
    auth.grant_role(session, user, Role.OBSERVER, actor_id=None)
    login(user.email)

    assert client.get("/horas").status_code == 403


def test_grid_page_shows_rows_and_totals(
    client: FlaskClient, session: Session, me: Resource, project: Project
) -> None:
    services.set_cell(
        session, me.id, RowKey(project_id=project.id), MONDAY, Decimal(8), actor_id=None
    )

    html = client.get(WEEK).get_data(as_text=True)

    assert "PROY-0001-2026 · Portal" in html
    assert 'value="8.00"' in html or 'value="8"' in html


def test_editing_a_cell_over_the_web_warns_past_ten_hours(
    client: FlaskClient, session: Session, me: Resource, project: Project
) -> None:
    response = post_cell(client, project, "2026-06-15", "10,5")

    assert response.status_code == 200
    assert "pasa de" in response.get_data(as_text=True)
    assert hours_of(session, me) == [Decimal("10.5")]


@pytest.mark.parametrize("raw", ["abc", "0,1", "30"])
def test_invalid_hours_are_reported_and_not_saved(
    client: FlaskClient, session: Session, me: Resource, project: Project, raw: str
) -> None:
    html = post_cell(client, project, "2026-06-15", raw).get_data(as_text=True)

    assert "text-red-800" in html
    assert hours_of(session, me) == []


def test_added_row_stays_visible_and_search_lists_results(
    client: FlaskClient, me: Resource, project: Project
) -> None:
    found = client.get("/horas/buscar?q=PROY-0001").get_data(as_text=True)
    assert f'value="{project.id},,,"' in found

    html = client.post(
        "/horas/fila",
        data={
            "nueva": f"{project.id},,,",
            "semana": "2026-06-15",
            "csrf_token": csrf_token(client, WEEK),
        },
        headers={"HX-Request": "true"},
    ).get_data(as_text=True)
    assert "PROY-0001-2026 · Portal" in html


def test_repeat_over_the_web(
    client: FlaskClient, session: Session, me: Resource, project: Project
) -> None:
    services.set_cell(
        session, me.id, RowKey(project_id=project.id), MONDAY, Decimal(6), actor_id=None
    )

    response = client.post(
        "/horas/repetir",
        data={"dia": "2026-06-16", "semana": "2026-06-15", "csrf_token": csrf_token(client, WEEK)},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert hours_of(session, me) == [Decimal(6), Decimal(6)]


def test_malformed_row_key_is_a_bad_request(client: FlaskClient, me: Resource) -> None:
    response = client.post(
        "/horas/celda",
        data={
            "fila": "x",
            "dia": "2026-06-15",
            "horas": "1",
            "csrf_token": csrf_token(client, WEEK),
        },
    )

    assert response.status_code == 400
