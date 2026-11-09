from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.agile import services
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.projects import services as projects
from hourplan.projects.models import (
    EpicStatus,
    Methodology,
    Project,
    ProjectKind,
    SprintStatus,
)
from hourplan.resources.models import Resource
from hourplan.timesheet.models import TimeEntry
from tests.conftest import csrf_token

Login = Callable[..., TestResponse]
MakeUser = Callable[..., User]


def project(
    session: Session, code: str = "PJ-00001", method: Methodology | None = Methodology.SCRUM
) -> Project:
    return projects.create_project(
        session,
        projects.ProjectData(code=code, title="Portal de ejemplo", methodology=method),
        actor_id=None,
    )


def sprint(session: Session, pid: int, name: str = "Sprint 1") -> int:
    return services.create_sprint(
        session,
        pid,
        services.SprintData(name=name, start_date=date(2026, 7, 6), end_date=date(2026, 7, 17)),
        actor_id=None,
    ).id


def story(session: Session, pid: int, code: str = "TK-00001", **extra: object) -> Project:
    return services.create_story(
        session,
        pid,
        services.StoryData(code=code, title="Historia", **extra),
        actor_id=None,  # type: ignore[arg-type]
    )


def test_methodology_defaults_to_waterfall_and_edit_keeps_it(session: Session) -> None:
    plain = project(session, method=None)
    assert plain.methodology == "waterfall"
    scrum = project(session, "PJ-00002")
    projects.update_project(
        session,
        scrum.id,
        projects.ProjectData(code="PJ-00002", title="Otro título"),
        actor_id=None,
    )
    assert scrum.methodology == "scrum"


def test_only_one_sprint_active_and_closed_is_history(session: Session) -> None:
    pj = project(session)
    first, second = sprint(session, pj.id), sprint(session, pj.id, "Sprint 2")
    services.start_sprint(session, pj.id, first, actor_id=None)
    with pytest.raises(services.AgileError, match="sprint activo"):
        services.start_sprint(session, pj.id, second, actor_id=None)
    services.close_sprint(session, pj.id, first, actor_id=None)
    services.start_sprint(session, pj.id, second, actor_id=None)
    with pytest.raises(services.AgileError):
        services.start_sprint(session, pj.id, first, actor_id=None)  # closed is not planned


def test_sprint_dates_and_names_are_validated(session: Session) -> None:
    pj = project(session)
    sprint(session, pj.id)
    with pytest.raises(services.AgileError, match="ese nombre"):
        sprint(session, pj.id, "SPRINT 1")
    with pytest.raises(ValueError, match="anterior"):
        services.SprintData(name="X", start_date=date(2026, 7, 10), end_date=date(2026, 7, 1))


def test_stories_only_join_epics_and_sprints_of_their_own_project(session: Session) -> None:
    mine, other = project(session), project(session, "PJ-00002")
    epic = services.create_epic(session, other.id, services.EpicData(name="Ajena"), actor_id=None)
    foreign_sprint = sprint(session, other.id)
    with pytest.raises(services.AgileError, match="Épica"):
        story(session, mine.id, epic_id=epic.id)
    with pytest.raises(services.AgileError, match="Sprint"):
        story(session, mine.id, sprint_id=foreign_sprint)


def test_database_rejects_a_foreign_epic_even_if_the_service_is_bypassed(session: Session) -> None:
    mine, other = project(session), project(session, "PJ-00002")
    epic = services.create_epic(session, other.id, services.EpicData(name="Ajena"), actor_id=None)
    item = story(session, mine.id)
    item.epic_id = epic.id
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_no_new_stories_in_a_closed_sprint(session: Session) -> None:
    pj = project(session)
    sid = sprint(session, pj.id)
    services.start_sprint(session, pj.id, sid, actor_id=None)
    services.close_sprint(session, pj.id, sid, actor_id=None)
    with pytest.raises(services.AgileError, match="cerrado"):
        story(session, pj.id, sprint_id=sid)


def test_totals_per_sprint_and_epic(session: Session) -> None:
    pj = project(session)
    sid = sprint(session, pj.id)
    epic = services.create_epic(session, pj.id, services.EpicData(name="E1"), actor_id=None)
    done = story(session, pj.id, "TK-00001", sprint_id=sid, epic_id=epic.id, base_effort_hours="4")
    story(session, pj.id, "TK-00002", sprint_id=sid, base_effort_hours="2.5")
    done.closed_on = date(2026, 7, 8)
    person = Resource(display_name="Persona de prueba")
    session.add(person)
    session.flush()
    session.add(
        TimeEntry(
            resource_id=person.id,
            work_date=date(2026, 7, 7),
            hours=Decimal("3.5"),
            project_id=done.id,
        )
    )
    session.commit()
    total = services.sprint_totals(session, pj.id)[sid]
    assert (total.stories, total.done, float(total.planned), float(total.actual)) == (
        2,
        1,
        6.5,
        3.5,
    )
    assert services.epic_totals(session, pj.id)[epic.id].stories == 1
    services.set_epic_status(session, pj.id, epic.id, EpicStatus.DONE, actor_id=None)
    assert SprintStatus.PLANNED.value == "planned"


def test_story_effort_must_be_a_quarter_multiple(session: Session) -> None:
    with pytest.raises(ValueError, match="0,25"):
        services.StoryData(code="TK-1", title="x", base_effort_hours="1.1")  # type: ignore[arg-type]


def _pm(session: Session, make_user: MakeUser, login: Login) -> None:
    user = make_user()
    auth.grant_role(session, user, Role.PM, actor_id=None)
    session.commit()
    login(user.email)


def test_screens_follow_the_methodology(
    session: Session, client: FlaskClient, make_user: MakeUser, login: Login
) -> None:
    scrum = project(session)
    waterfall = project(session, "PJ-00002", Methodology.WATERFALL)
    kanban = project(session, "PJ-00003", Methodology.KANBAN)
    _pm(session, make_user, login)
    assert client.get(f"/proyectos/{scrum.id}/agil/sprints").status_code == 200
    assert client.get(f"/proyectos/{scrum.id}/agil/epicas").status_code == 200
    assert client.get(f"/proyectos/{kanban.id}/agil/tablero").status_code == 200
    assert client.get(f"/proyectos/{kanban.id}/agil/sprints").status_code == 404
    assert client.get(f"/proyectos/{waterfall.id}/agil/tablero").status_code == 404
    detail = client.get(f"/proyectos/{waterfall.id}").get_data(as_text=True)
    assert "Fases" in detail
    detail = client.get(f"/proyectos/{scrum.id}").get_data(as_text=True)
    assert "Trabajo ágil" in detail
    assert 'aria-label="Fases del proyecto"' not in detail


def test_create_story_and_sprint_through_the_screens(
    session: Session, client: FlaskClient, make_user: MakeUser, login: Login
) -> None:
    pj = project(session)
    _pm(session, make_user, login)
    base = f"/proyectos/{pj.id}/agil"
    client.post(
        f"{base}/sprints",
        data={
            "nombre": "S1",
            "inicio": "2026-07-06",
            "fin": "2026-07-17",
            "csrf_token": csrf_token(client, f"{base}/sprints"),
        },
    )
    page = client.get(f"{base}/sprints").get_data(as_text=True)
    assert "S1" in page
    client.post(
        f"{base}/historias",
        data={
            "codigo": "TK-00009",
            "titulo": "Login",
            "esfuerzo": "3",
            "csrf_token": csrf_token(client, f"{base}/tablero"),
        },
    )
    board = client.get(f"{base}/tablero").get_data(as_text=True)
    assert "TK-00009" in board
    assert session.query(Project).filter_by(kind=ProjectKind.TICKET.value).count() == 1


def test_observer_cannot_change_anything(
    session: Session, client: FlaskClient, make_user: MakeUser, login: Login
) -> None:
    pj = project(session)
    user = make_user()
    auth.grant_role(session, user, Role.OBSERVER, actor_id=None)
    session.commit()
    login(user.email)
    base = f"/proyectos/{pj.id}/agil"
    assert client.get(f"{base}/tablero").status_code == 200
    token = csrf_token(client, f"{base}/tablero")
    assert (
        client.post(f"{base}/epicas", data={"nombre": "X", "csrf_token": token}).status_code == 403
    )


# ------------------------------------------------------------------ points, limits, closing
def statuses(session: Session) -> dict[str, int]:
    for code, name, closed in (("DOING", "En curso", False), ("DONE", "Terminado", True)):
        session.add(
            CatalogItem(
                kind=CatalogKind.PROJECT_STATUS.value,
                code=code,
                name=name,
                attributes={"is_closed": True} if closed else {},
            )
        )
    session.flush()
    rows = session.scalars(
        select(CatalogItem).where(CatalogItem.kind == CatalogKind.PROJECT_STATUS.value)
    )
    return {item.code: item.id for item in rows}


def test_moving_to_a_closing_status_finishes_the_story_and_leaving_reopens_it(
    session: Session,
) -> None:
    pj = project(session)
    st = statuses(session)
    one = story(session, pj.id)
    services.move_story(session, pj.id, one.id, st["DONE"], actor_id=None, on=date(2026, 7, 9))
    assert one.closed_on == date(2026, 7, 9)
    services.move_story(session, pj.id, one.id, st["DONE"], actor_id=None, on=date(2026, 7, 10))
    assert one.closed_on == date(2026, 7, 9)
    services.move_story(session, pj.id, one.id, st["DOING"], actor_id=None)
    assert one.closed_on is None


def test_wip_limit_stops_a_full_column(session: Session) -> None:
    pj = project(session)
    st = statuses(session)
    first, second = story(session, pj.id, "TK-00001"), story(session, pj.id, "TK-00002")
    services.set_wip_limits(session, pj.id, {st["DOING"]: 1}, actor_id=None)
    assert services.wip_limits(session, pj.id) == {st["DOING"]: 1}
    services.move_story(session, pj.id, first.id, st["DOING"], actor_id=None)
    services.move_story(session, pj.id, first.id, st["DOING"], actor_id=None)  # stays put
    with pytest.raises(services.AgileError, match="límite"):
        services.move_story(session, pj.id, second.id, st["DOING"], actor_id=None)
    with pytest.raises(services.AgileError, match="entre 1 y 999"):
        services.set_wip_limits(session, pj.id, {st["DOING"]: 0}, actor_id=None)
    services.set_wip_limits(session, pj.id, {st["DOING"]: None}, actor_id=None)
    assert services.wip_limits(session, pj.id) == {}
    services.move_story(session, pj.id, second.id, st["DOING"], actor_id=None)


def test_points_are_validated_and_added_up(session: Session) -> None:
    with pytest.raises(ValueError, match="100"):
        services.StoryData(code="TK-1", title="x", story_points=101)
    pj = project(session)
    sid = sprint(session, pj.id)
    done = story(session, pj.id, "TK-00001", sprint_id=sid, story_points=5)
    other = story(session, pj.id, "TK-00002", sprint_id=sid)
    services.assign_story(
        session, pj.id, other.id, epic_id=None, sprint_id=sid, story_points=3, actor_id=None
    )
    with pytest.raises(services.AgileError, match="Puntos"):
        services.assign_story(
            session, pj.id, other.id, epic_id=None, sprint_id=sid, story_points=-1, actor_id=None
        )
    done.closed_on = date(2026, 7, 8)
    total = services.sprint_totals(session, pj.id)[sid]
    assert (total.points, total.points_done) == (8, 5)


def test_closing_a_sprint_carries_unfinished_stories_and_keeps_its_history(
    session: Session,
) -> None:
    pj = project(session)
    first = sprint(session, pj.id, "Sprint 1")
    second = sprint(session, pj.id, "Sprint 2")
    done = story(session, pj.id, "TK-00001", sprint_id=first, story_points=5)
    left = story(session, pj.id, "TK-00002", sprint_id=first, story_points=3)
    done.closed_on = date(2026, 7, 10)
    services.start_sprint(session, pj.id, first, actor_id=None)
    with pytest.raises(services.AgileError, match="planificado"):
        services.close_sprint(session, pj.id, first, carry_to=first, actor_id=None)
    services.close_sprint(session, pj.id, first, carry_to=second, actor_id=None)
    assert (done.sprint_id, left.sprint_id) == (first, second)
    held = services.closed_history(session, first)
    assert [(h.story_id, h.carried_to_id) for h in held] == [(done.id, None), (left.id, second)]
    totals = services.sprint_totals(session, pj.id)
    assert (totals[first].stories, totals[first].done, totals[first].points) == (2, 1, 8)
    assert totals[second].stories == 1


def test_closing_without_a_target_sends_unfinished_work_to_the_backlog(session: Session) -> None:
    pj = project(session)
    sid = sprint(session, pj.id)
    left = story(session, pj.id, sprint_id=sid)
    services.start_sprint(session, pj.id, sid, actor_id=None)
    services.close_sprint(session, pj.id, sid, actor_id=None)
    assert left.sprint_id is None


def test_burndown_in_points_with_ideal_and_actual_lines(session: Session) -> None:
    pj = project(session)
    sid = sprint(session, pj.id)  # 2026-07-06 .. 2026-07-17
    done = story(session, pj.id, "TK-00001", sprint_id=sid, story_points=5, base_effort_hours="8")
    story(session, pj.id, "TK-00002", sprint_id=sid, story_points=3)
    done.closed_on = date(2026, 7, 8)
    chart = services.burndown(session, pj.id, sid, today=date(2026, 7, 9))
    assert (chart.unit, chart.scope, len(chart.days)) == ("points", 8, 12)
    assert (chart.ideal[0], chart.ideal[-1]) == (8, 0)
    assert chart.remaining == [8, 8, 3, 3]


def test_burndown_falls_back_to_hours_without_points(session: Session) -> None:
    pj = project(session)
    sid = sprint(session, pj.id)
    story(session, pj.id, sprint_id=sid, base_effort_hours="6")
    chart = services.burndown(session, pj.id, sid, today=date(2026, 7, 1))
    assert (chart.unit, chart.scope, chart.remaining) == ("hours", 6, [])


def test_screens_show_limits_points_and_burndown_in_english(
    session: Session, client: FlaskClient, make_user: MakeUser, login: Login
) -> None:
    pj = project(session)
    st = statuses(session)
    sid = sprint(session, pj.id)
    story(session, pj.id, sprint_id=sid, story_points=5)
    services.start_sprint(session, pj.id, sid, actor_id=None)
    _pm(session, make_user, login)
    base = f"/proyectos/{pj.id}/agil"
    client.post(
        f"{base}/limites",
        data={f"limite-{st['DOING']}": "2", "csrf_token": csrf_token(client, f"{base}/tablero")},
    )
    assert services.wip_limits(session, pj.id) == {st["DOING"]: 2}
    client.set_cookie("lang", "en")
    board = client.get(f"{base}/tablero").get_data(as_text=True)
    assert "Work-in-progress limits" in board
    assert "5 pts" in board
    page = client.get(f"{base}/sprints").get_data(as_text=True)
    assert "Progress of Sprint 1" in page
    assert "<polyline" in page
    assert "Move unfinished work to" in page
