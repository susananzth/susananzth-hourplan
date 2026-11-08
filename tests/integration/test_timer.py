from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.timer import alerts, services
from hourplan.timer.models import TimerSession, TimerStatus
from hourplan.timesheet.models import EntrySource, TimeEntry
from hourplan.timesheet.services import RowKey
from tests.conftest import csrf_token

T0 = datetime(2026, 6, 15, 14, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


@pytest.fixture
def resource(session: Session) -> Resource:
    item = Resource(display_name="Ana")
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def project(session: Session) -> Project:
    item = Project(
        code="PJ-00001",
        title="Portal",
        base_effort_hours=Decimal(80),
        contingency_hours=Decimal(20),
    )
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def other(session: Session) -> Project:
    item = Project(code="PJ-00002", title="Otro")
    session.add(item)
    session.commit()
    return item


def beats(session: Session, resource: Resource, start: float, end: float) -> None:
    """The browser stays open: one heartbeat a minute."""
    minute = start
    while minute <= end:
        services.heartbeat(session, resource.id, at(minute))
        minute += 1


def test_rounds_to_nearest_quarter() -> None:
    assert services.hours_for(7 * 60) == Decimal("0.00")
    assert services.hours_for(8 * 60) == Decimal("0.25")
    assert services.hours_for(3600 + 20 * 60) == Decimal("1.25")
    assert services.hours_for(30 * 3600) == Decimal(24)


def test_stop_creates_timer_entry(session: Session, resource: Resource, project: Project) -> None:
    timer = services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    beats(session, resource, 1, 62)
    entry = services.stop(session, resource.id, timer.id, at(62), actor_id=None)
    assert entry.hours == Decimal("1.00")
    assert entry.source == EntrySource.TIMER
    assert session.get(TimerSession, timer.id).status == TimerStatus.STOPPED


def test_heartbeat_keeps_time_and_silence_pauses_at_last_beat(
    session: Session, resource: Resource, project: Project
) -> None:
    timer = services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    services.heartbeat(session, resource.id, at(1))
    services.heartbeat(session, resource.id, at(2))
    # Browser closed: 1 hour later the timer shows only up to the last heartbeat.
    services.settle(session, resource.id, at(60))
    assert services.running(session, resource.id, at(60)) is None
    session.refresh(timer)
    assert timer.status == TimerStatus.PAUSED
    assert services.elapsed_seconds(timer, at(60)) == 120


def test_new_timer_pauses_the_running_one(
    session: Session, resource: Resource, project: Project, other: Project
) -> None:
    first = services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    services.heartbeat(session, resource.id, at(1))
    second = services.start(session, resource.id, RowKey(project_id=other.id), at(2))
    session.refresh(first)
    assert first.status == TimerStatus.PAUSED
    assert services.elapsed_seconds(first, at(2)) == 120
    assert second.status == TimerStatus.RUNNING
    # Back to the first task resumes it instead of opening a second row.
    again = services.start(session, resource.id, RowKey(project_id=project.id), at(5))
    assert again.id == first.id


def test_total_never_exceeds_the_clock(
    session: Session, resource: Resource, project: Project, other: Project
) -> None:
    a = services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    services.heartbeat(session, resource.id, at(1))
    services.heartbeat(session, resource.id, at(2))
    b = services.start(session, resource.id, RowKey(project_id=other.id), at(2))
    services.heartbeat(session, resource.id, at(3))
    total = services.elapsed_seconds(a, at(3)) + services.elapsed_seconds(b, at(3))
    assert total <= 3 * 60


def test_interruption_is_classified_and_task_continues(
    session: Session, resource: Resource, project: Project, other: Project
) -> None:
    task = services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    beats(session, resource, 1, 10)
    interruption = services.interrupt(session, resource.id, at(10))
    session.refresh(task)
    assert task.status == TimerStatus.PAUSED
    beats(session, resource, 11, 25)
    with pytest.raises(services.TimerError, match="Clasifica"):
        services.stop(session, resource.id, interruption.id, at(25), actor_id=None)
    entry = services.stop(
        session,
        resource.id,
        interruption.id,
        at(25),
        key=RowKey(project_id=other.id),
        actor_id=None,
    )
    assert entry.project_id == other.id
    session.refresh(task)
    assert task.status == TimerStatus.RUNNING


def test_too_short_cannot_be_saved(session: Session, resource: Resource, project: Project) -> None:
    timer = services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    with pytest.raises(services.TimerError, match="menos de un minuto"):
        services.stop(session, resource.id, timer.id, at(0.5), actor_id=None)
    services.discard(session, resource.id, timer.id, at(0.5))
    assert session.scalars(select(TimeEntry)).first() is None


def test_idle_break_is_discounted_other_choices_are_noted(
    session: Session, resource: Resource, project: Project
) -> None:
    timer = services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    beats(session, resource, 1, 10)
    services.apply_idle(session, resource.id, 6 * 60, "break", at(10))
    assert services.elapsed_seconds(timer, at(10)) == 4 * 60
    services.apply_idle(session, resource.id, 120, "meeting", at(10))
    assert "Reunión" in (timer.description or "")
    with pytest.raises(services.TimerError):
        services.apply_idle(session, resource.id, 1, "other", at(10))


def test_single_running_timer_enforced_by_database(
    session: Session, resource: Resource, project: Project
) -> None:
    from sqlalchemy.exc import IntegrityError

    for _ in range(2):
        session.add(
            TimerSession(
                resource_id=resource.id,
                status="running",
                started_at=T0,
                segment_started_at=T0,
                last_beat_at=T0,
                project_id=project.id,
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_idle_minutes_preference(session: Session, resource: Resource) -> None:
    assert services.idle_minutes(session, resource.id) == 5
    services.set_idle_minutes(session, resource.id, 12)
    assert services.idle_minutes(session, resource.id) == 12
    with pytest.raises(services.TimerError):
        services.set_idle_minutes(session, resource.id, 0)


# ----------------------------------------------------------------------------- alerts


def test_parse_thresholds() -> None:
    assert alerts.parse_thresholds("90, 50 75%") == [50, 75, 90]
    assert alerts.parse_thresholds("  ") == []
    with pytest.raises(alerts.AlertError, match="no es un porcentaje"):
        alerts.parse_thresholds("mucho")
    with pytest.raises(alerts.AlertError):
        alerts.parse_thresholds("0")


def test_personal_thresholds_replace_the_template(
    session: Session, resource: Resource, project: Project
) -> None:
    alerts.set_thresholds(session, project.id, None, [90])
    assert alerts.effective(session, project.id, resource.id) == [90]
    alerts.set_thresholds(session, project.id, resource.id, [70])
    assert alerts.effective(session, project.id, resource.id) == [70]
    alerts.set_thresholds(session, project.id, resource.id, [])
    assert alerts.effective(session, project.id, resource.id) == []
    alerts.set_thresholds(session, project.id, resource.id, None)
    assert alerts.effective(session, project.id, resource.id) == [90]


def log(session: Session, resource: Resource, project: Project, hours: str) -> None:
    left = Decimal(hours)
    day = 0
    while left > 0:
        chunk = min(left, Decimal(8))
        session.add(
            TimeEntry(
                resource_id=resource.id,
                work_date=T0.date() - timedelta(days=day),
                hours=chunk,
                project_id=project.id,
            )
        )
        left -= chunk
        day += 1
    session.commit()


def test_alert_fires_once_per_threshold_at_each_persons_level(
    session: Session, resource: Resource, project: Project
) -> None:
    partner = Resource(display_name="Beto")
    session.add(partner)
    session.commit()
    alerts.set_thresholds(session, project.id, None, [50, 90])  # budget 100 h
    alerts.set_thresholds(session, project.id, partner.id, [70])
    log(session, resource, project, "23")
    log(session, partner, project, "1")
    assert alerts.pending(session, resource.id, T0, T0.date()) == []
    log(session, resource, project, "30")  # 54 %
    first = alerts.pending(session, resource.id, T0, T0.date())
    assert [(a.percent, a.consumed_pct) for a in first] == [(50, 54)]
    assert alerts.pending(session, resource.id, T0, T0.date()) == []
    # Beto only wants 70 %: 54 % does not warn him, 76 % does.
    assert alerts.pending(session, partner.id, T0, T0.date()) == []
    log(session, resource, project, "22")  # 76 %
    assert [a.percent for a in alerts.pending(session, partner.id, T0, T0.date())] == [70]
    assert alerts.pending(session, resource.id, T0, T0.date()) == []
    log(session, resource, project, "15")  # 91 %
    assert [a.percent for a in alerts.pending(session, resource.id, T0, T0.date())] == [90]


def test_running_timer_counts_toward_the_alert(
    session: Session, resource: Resource, project: Project
) -> None:
    alerts.set_thresholds(session, project.id, None, [50])
    log(session, resource, project, "49")
    services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    beats(session, resource, 1, 30)
    assert alerts.pending(session, resource.id, at(30)) == []  # 49.5 h of 100
    beats(session, resource, 31, 90)
    assert [a.percent for a in alerts.pending(session, resource.id, at(90))] == [50]


def test_project_without_hours_has_no_alerts(
    session: Session, resource: Resource, other: Project
) -> None:
    alerts.set_thresholds(session, other.id, None, [50])
    log(session, resource, other, "10")
    assert alerts.pending(session, resource.id, T0, T0.date()) == []


# ----------------------------------------------------------------------------- views


@pytest.fixture
def me(
    session: Session, make_user: Callable[..., User], login: Callable[..., TestResponse]
) -> Resource:
    user = make_user()
    auth.grant_role(session, user, Role.DEVELOPER, actor_id=None)
    item = Resource(display_name="Ana", user_id=user.id)
    session.add(item)
    session.commit()
    assert login(user.email).status_code == 302
    return item


def post(client, path: str, **data: str):  # type: ignore[no-untyped-def]
    return client.post(path, data={"csrf_token": csrf_token(client, "/cronometro"), **data})


def test_page_start_pause_and_stop(
    client, me: Resource, project: Project, session: Session
) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/cronometro").status_code == 200
    resp = post(client, "/cronometro/iniciar", nueva=f"{project.id},,,")
    assert resp.status_code == 302
    page = client.get("/cronometro").get_data(as_text=True)
    assert "PJ-00001" in page
    assert "Corriendo" in page
    timer = session.scalars(select(TimerSession)).one()
    beat = post(client, "/cronometro/latido")
    assert 'id="timer-bar"' in beat.get_data(as_text=True)
    assert "Pausar" in beat.get_data(as_text=True)
    post(client, f"/cronometro/pausar/{timer.id}")
    session.refresh(timer)
    assert timer.status == TimerStatus.PAUSED
    assert "Reanudar" in client.get("/cronometro").get_data(as_text=True)


def test_start_without_choice_shows_error(client, me: Resource) -> None:  # type: ignore[no-untyped-def]
    resp = post(client, "/cronometro/iniciar")
    assert "Elige un proyecto" in client.get(resp.headers["Location"]).get_data(as_text=True)


def test_idle_endpoint(client, me: Resource, project: Project, session: Session) -> None:  # type: ignore[no-untyped-def]
    post(client, "/cronometro/iniciar", nueva=f"{project.id},,,")
    assert (
        post(client, "/cronometro/inactividad", opcion="assist", segundos="300").status_code == 204
    )
    assert post(client, "/cronometro/inactividad", opcion="x", segundos="300").status_code == 400
    assert "compañero" in (session.scalars(select(TimerSession)).one().description or "")


def test_template_needs_manager_or_admin(
    client, me: Resource, project: Project, session: Session
) -> None:  # type: ignore[no-untyped-def]
    resp = post(client, f"/cronometro/alertas/{project.id}/plantilla", **{f"p{project.id}": "90"})
    assert resp.status_code == 403
    project.lead_id = me.user_id
    session.commit()
    resp = post(client, f"/cronometro/alertas/{project.id}/plantilla", **{f"p{project.id}": "90"})
    assert resp.status_code == 302
    assert alerts.template(session, project.id) == [90]
    post(client, f"/cronometro/alertas/{project.id}/mio", **{f"m{project.id}": "70"})
    assert alerts.personal(session, project.id, me.id) == [70]
    page = client.get("/cronometro/alertas").get_data(as_text=True)
    assert "PJ-00001" in page


def test_observer_has_no_timer(
    client,
    session: Session,
    make_user: Callable[..., User],
    login: Callable[..., TestResponse],  # type: ignore[no-untyped-def]
) -> None:
    user = make_user()
    auth.grant_role(session, user, Role.OBSERVER, actor_id=None)
    login(user.email)
    assert client.get("/cronometro").status_code == 403


# --------------------------------------------------------------------------- switching


def test_switch_saves_the_task_and_keeps_the_clock(
    session: Session, resource: Resource, project: Project, other: Project
) -> None:
    first = services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    beats(session, resource, 1, 10)
    entry, second = services.switch(
        session, resource.id, RowKey(project_id=other.id), at(10), actor_id=None
    )
    assert entry is not None
    assert entry.project_id == project.id
    assert entry.hours == Decimal("0.25")
    session.refresh(first)
    assert first.status == TimerStatus.STOPPED
    assert second.status == TimerStatus.RUNNING
    assert second.project_id == other.id
    assert second.chain_seconds == 600
    beats(session, resource, 11, 15)
    assert second.chain_seconds + services.elapsed_seconds(second, at(15)) == 900


def test_switch_under_a_minute_saves_nothing(
    session: Session, resource: Resource, project: Project, other: Project
) -> None:
    services.start(session, resource.id, RowKey(project_id=project.id), at(0))
    entry, second = services.switch(
        session, resource.id, RowKey(project_id=other.id), at(0.5), actor_id=None
    )
    assert entry is None
    assert session.scalars(select(TimeEntry)).all() == []
    assert second.chain_seconds == 30


def test_switch_classifies_an_unclassified_interruption(
    session: Session, resource: Resource, project: Project
) -> None:
    timer = services.interrupt(session, resource.id, at(0))
    beats(session, resource, 1, 5)
    entry, same = services.switch(
        session, resource.id, RowKey(project_id=project.id), at(5), actor_id=None
    )
    assert entry is None
    assert same.id == timer.id
    assert same.project_id == project.id
    assert same.status == TimerStatus.RUNNING


def test_switch_without_running_timer_just_starts(
    session: Session, resource: Resource, project: Project
) -> None:
    entry, timer = services.switch(
        session, resource.id, RowKey(project_id=project.id), at(0), actor_id=None
    )
    assert entry is None
    assert timer.status == TimerStatus.RUNNING
    with pytest.raises(services.TimerError):
        services.switch(session, resource.id, RowKey(), at(1), actor_id=None)


def hx(client, path: str, **data: str):  # type: ignore[no-untyped-def]
    return client.post(
        path,
        data={"csrf_token": csrf_token(client, "/cronometro"), **data},
        headers={"HX-Request": "true"},
    )


def test_bar_starts_switches_and_ends_in_place(
    client, me: Resource, project: Project, other: Project, session: Session
) -> None:  # type: ignore[no-untyped-def]
    idle = hx(client, "/cronometro/latido").get_data(as_text=True)
    assert "¿En qué trabajas?" in idle
    assert "Interrupción" not in idle  # only offered while something runs

    started = hx(client, "/cronometro/iniciar", nueva=f"{project.id},,,")
    assert started.status_code == 200
    html = started.get_data(as_text=True)
    assert 'id="timer-bar"' in html
    assert "Cambiar tarea" in html
    assert "Terminar" in html
    first = session.scalars(select(TimerSession)).one()
    first.segment_started_at = first.started_at = first.segment_started_at - timedelta(minutes=20)
    first.last_beat_at = first.segment_started_at + timedelta(minutes=20)
    session.commit()

    switched = hx(client, "/cronometro/cambiar", nueva=f"{other.id},,,").get_data(as_text=True)
    assert "Guardado: PJ-00001" in switched
    assert "en esta tarea" in switched
    current = session.scalars(
        select(TimerSession).where(TimerSession.status == TimerStatus.RUNNING)
    ).one()
    assert current.project_id == other.id

    # Under a minute it cannot be saved; the error shows inside the bar.
    early = hx(client, f"/cronometro/detener/{current.id}").get_data(as_text=True)
    assert "menos de un minuto" in early
    assert 'role="alert"' in early

    gone = hx(client, f"/cronometro/descartar/{current.id}").get_data(as_text=True)
    assert "Cronómetro descartado" in gone
    assert "¿En qué trabajas?" in gone


def test_bar_stop_keeps_the_task_type(
    client, me: Resource, project: Project, session: Session
) -> None:  # type: ignore[no-untyped-def]
    task_type = CatalogItem(kind=CatalogKind.TASK_TYPE.value, code="SUP", name="Soporte")
    session.add(task_type)
    session.commit()
    hx(client, "/cronometro/iniciar", nueva=f"{project.id},,,")
    timer = session.scalars(select(TimerSession)).one()
    timer.segment_started_at = timer.started_at = timer.started_at - timedelta(minutes=30)
    timer.last_beat_at = timer.segment_started_at + timedelta(minutes=30)
    session.commit()
    done = hx(client, f"/cronometro/detener/{timer.id}", tipo=str(task_type.id), descripcion="Ok")
    assert "Guardado: PJ-00001 · Portal · 0,50 h" in done.get_data(as_text=True)
    entry = session.scalars(select(TimeEntry)).one()
    assert entry.task_type_id == task_type.id
    assert entry.description == "Ok"


def test_paused_timer_stays_in_the_bar(
    client, me: Resource, project: Project, session: Session
) -> None:  # type: ignore[no-untyped-def]
    hx(client, "/cronometro/iniciar", nueva=f"{project.id},,,")
    timer = session.scalars(select(TimerSession)).one()
    paused = hx(client, f"/cronometro/pausar/{timer.id}").get_data(as_text=True)
    assert "Reanudar" in paused
    assert "En pausa" in paused
    assert "Terminar" in paused
    assert "Cambiar tarea" not in paused
