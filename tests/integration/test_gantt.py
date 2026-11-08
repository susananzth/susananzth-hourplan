from collections.abc import Callable
from datetime import date
from decimal import Decimal
from io import BytesIO

from flask.testing import FlaskClient
from openpyxl import load_workbook
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogItem
from hourplan.planning import gantt, services
from hourplan.planning.models import PlanAllocation
from hourplan.projects import services as projects
from hourplan.projects.models import PhaseStatus
from hourplan.resources.models import Resource
from hourplan.timesheet.models import Absence, ApprovalStatus, TimeEntry

MONDAY = date(2026, 7, 6)
TODAY = date(2026, 7, 8)
Login = Callable[..., TestResponse]
MakeUser = Callable[..., User]


def world(session: Session, user_id: int | None = None) -> tuple[int, Resource]:
    project = projects.create_project(
        session, projects.ProjectData(code="PJ-00001", title="Portal"), actor_id=None
    )
    project.phases.append(
        projects.ProjectPhase(position=1, name="Desarrollo", phase_type="construction")
    )
    resource = Resource(display_name="Persona A", user_id=user_id)
    bucket = CatalogItem(kind="activity_bucket", code="MEET", name="Reuniones")
    session.add_all([resource, bucket])
    session.commit()
    phase = project.phases[0]
    projects.update_phase(
        session,
        phase.id,
        projects.PhaseData(
            status=PhaseStatus.IN_PROGRESS, planned_start=MONDAY, planned_end=date(2026, 7, 17)
        ),
        actor_id=None,
    )
    for hours in ("30", "30"):
        services.add_allocation(
            session,
            project.id,
            services.AllocationData(
                resource_id=resource.id,
                project_phase_id=phase.id,
                start_on=MONDAY,
                end_on=date(2026, 7, 10),
                hours=Decimal(hours),
            ),
            actor_id=None,
        )
    session.add_all(
        [
            PlanAllocation(
                activity_bucket_id=bucket.id,
                resource_id=resource.id,
                start_on=date(2026, 7, 13),
                end_on=date(2026, 7, 17),
                hours=Decimal(5),
            ),
            Absence(
                resource_id=resource.id,
                absence_type="vacation",
                start_on=date(2026, 7, 20),
                end_on=date(2026, 7, 21),
                status=ApprovalStatus.APPROVED.value,
            ),
            TimeEntry(
                resource_id=resource.id,
                work_date=MONDAY,
                hours=Decimal(6),
                project_id=project.id,
                project_phase_id=phase.id,
            ),
            TimeEntry(
                resource_id=resource.id,
                work_date=date(2026, 7, 7),
                hours=Decimal(2),
                project_id=project.id,
                project_phase_id=phase.id,
            ),
            TimeEntry(
                resource_id=resource.id,
                work_date=MONDAY,
                hours=Decimal(1),
                activity_bucket_id=bucket.id,
            ),
        ]
    )
    session.commit()
    return project.id, resource


def test_timeline_geometry() -> None:
    tl = gantt.Timeline.weeks_from(date(2026, 7, 8), 2)
    assert (tl.start, tl.end) == (MONDAY, date(2026, 7, 19))
    assert tl.x(MONDAY) == 0
    assert tl.span(gantt.Bar(date(2026, 6, 1), date(2026, 7, 12), "x", "plan")) == (0, 50)
    assert len(tl.weeks()) == 2
    assert not tl.overlaps(gantt.Bar(date(2026, 8, 1), date(2026, 8, 2), "x", "plan"))


def test_stack_puts_overlaps_on_separate_lanes() -> None:
    a = gantt.Bar(MONDAY, date(2026, 7, 10), "a", "plan")
    b = gantt.Bar(date(2026, 7, 8), date(2026, 7, 9), "b", "plan")
    c = gantt.Bar(date(2026, 7, 13), date(2026, 7, 14), "c", "plan")
    assert [bar.lane for bar in gantt.stack([a, b, c])] == [0, 1, 0]


def test_project_rows_show_plan_and_actual(session: Session) -> None:
    world(session)
    rows = gantt.project_rows(session, gantt.Timeline.weeks_from(MONDAY, 4), today=TODAY)

    head, phase = rows
    assert head.label == "PJ-00001 · Portal"
    assert {b.kind for b in head.bars} == {"plan", "actual"}
    assert head.hours == Decimal(60)
    actual = next(b for b in phase.bars if b.kind == "actual")
    assert (actual.start, actual.end, actual.hours) == (MONDAY, date(2026, 7, 7), Decimal(8))
    assert (
        gantt.project_rows(session, gantt.Timeline.weeks_from(date(2027, 1, 4), 2), today=TODAY)
        == []
    )


def test_resource_rows_mark_overloaded_weeks(session: Session) -> None:
    _, resource = world(session)
    (row,) = gantt.resource_rows(session, gantt.Timeline.weeks_from(MONDAY, 3), [resource.id])

    assert {b.kind for b in row.bars} == {"plan", "bucket", "absence"}
    assert row.lanes == 2
    # 60 h planned in a 40 h week.
    assert [(m.start, m.hours) for m in row.markers] == [(MONDAY, Decimal(20))]


def test_personal_rows_group_projects_buckets_and_absences(session: Session) -> None:
    _, resource = world(session)
    rows = gantt.personal_rows(session, gantt.Timeline.weeks_from(MONDAY, 3), resource.id)

    assert [r.label for r in rows] == ["PJ-00001 · Portal", "Actividad: Reuniones", "Ausencias"]
    assert {b.kind for b in rows[1].bars} == {"bucket", "actual"}


def test_export_workbook(session: Session) -> None:
    world(session)
    tl = gantt.Timeline.weeks_from(MONDAY, 2)
    data = gantt.to_workbook("Gantt", tl, gantt.project_rows(session, tl, today=TODAY))
    sheet = load_workbook(BytesIO(data)).active
    assert sheet is not None
    assert sheet["A2"].value == "Fila"
    plan = next(r for r in range(3, sheet.max_row + 1) if sheet.cell(r, 2).value == "Planificado")
    assert sheet.cell(plan, 1).value == "PJ-00001 · Portal"
    assert sheet.cell(plan, 7).fill.fgColor.rgb.endswith("3B82F6")


def test_gantt_pages(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    user = make_user()
    auth.grant_role(session, user, Role.LEAD, actor_id=None)
    session.commit()
    world(session, user.id)
    login(user.email)

    html = client.get("/planificacion/gantt/proyectos?desde=2026-07-06&semanas=4").get_data(
        as_text=True
    )
    assert "PJ-00001 · Portal" in html
    assert "<rect" in html
    assert 'style="' not in html
    assert "Persona A" in client.get("/planificacion/gantt/recursos?desde=2026-07-06").get_data(
        as_text=True
    )
    assert "Reuniones" in client.get("/planificacion/gantt/mio?desde=2026-07-06").get_data(
        as_text=True
    )
    export = client.get("/planificacion/gantt/recursos?desde=2026-07-06&formato=xlsx")
    assert export.status_code == 200
    assert "spreadsheetml" in export.mimetype


def test_developer_sees_projects_and_own_plan_only(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    user = make_user()
    auth.grant_role(session, user, Role.DEVELOPER, actor_id=None)
    session.commit()
    login(user.email)

    assert client.get("/planificacion/gantt/proyectos").status_code == 200
    assert client.get("/planificacion/gantt/recursos").status_code == 403
    assert "no está vinculado" in client.get("/planificacion/gantt/mio").get_data(as_text=True)
