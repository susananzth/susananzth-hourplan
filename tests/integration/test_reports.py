from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from flask.testing import FlaskClient
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import Application, CatalogItem
from hourplan.planning import services as planning
from hourplan.policies import services as policies
from hourplan.projects import cycles
from hourplan.projects import services as projects
from hourplan.projects.models import (
    CycleOutcome,
    LinkType,
    PhaseTemplate,
    PhaseTemplateStep,
    Project,
    ProjectKind,
    WorkItemLink,
)
from hourplan.reports import compare, dashboard, notes
from hourplan.reports.models import Compliance
from hourplan.resources.models import Resource
from hourplan.timesheet.models import TimeEntry
from hourplan.timesheet.services import EntryData, create_entry
from tests.conftest import csrf_token

Login = Callable[..., TestResponse]
MakeUser = Callable[..., User]
D = date  # shorthand for the test calendar
JAN_1 = D(2026, 1, 1)
START, END = D(2026, 7, 1), D(2026, 7, 31)


class World:
    """A project with 40 h planned and 48 h worked, 16 of them rework after a returned round."""

    def __init__(self, session: Session) -> None:
        self.session = session
        template = PhaseTemplate(code="STD", name="Estándar", is_default=True)
        template.steps = [
            PhaseTemplateStep(position=1, name="Desarrollo", phase_type="construction"),
            PhaseTemplateStep(position=2, name="Pruebas", phase_type="verification"),
        ]
        session.add(template)
        session.commit()
        self.project = projects.create_project(
            session, projects.ProjectData(code="PJ-00001", title="Portal"), actor_id=None
        )
        self.build, self.test = self.project.phases
        self.resource = Resource(display_name="Persona A")
        self.category = CatalogItem(kind="rework_category", code="EXEC", name="Ejecución")
        self.app = Application(code="APP-A", name="Aplicación A")
        session.add_all([self.resource, self.category, self.app])
        session.commit()

        planning.add_allocation(
            session,
            self.project.id,
            planning.AllocationData(
                resource_id=self.resource.id,
                project_phase_id=self.build.id,
                start_on=D(2026, 7, 6),
                end_on=D(2026, 7, 10),
                hours=Decimal(40),
            ),
            actor_id=None,
        )
        planning.set_baseline(session, self.project.id, actor_id=None)
        for day in (6, 7, 8):
            self.log(day, 8, self.build)
        self.log(13, 8, self.test)
        cycle = cycles.submit(session, self.test.id, D(2026, 7, 13), actor_id=None)
        cycles.decide(
            session,
            cycle.id,
            cycles.Decision(
                outcome=CycleOutcome.RETURNED,
                resolved_on=D(2026, 7, 14),
                observations="Falla",
                rework_category_id=self.category.id,
            ),
            decider_id=None,
        )
        self.log(15, 8, self.build)
        self.log(16, 8, self.build)

    def log(self, day: int, hours: int, phase: object, **extra: object) -> TimeEntry:
        return create_entry(
            self.session,
            self.resource.id,
            EntryData(
                work_date=D(2026, 7, day),
                hours=Decimal(hours),
                project_id=self.project.id,
                project_phase_id=phase.id,  # type: ignore[attr-defined]
                **extra,  # type: ignore[arg-type]
            ),
            actor_id=None,
        )

    def policy(self, tolerance: str, red: str, *, since: date = JAN_1, colors: bool = True):
        data = policies.EvaluationPolicyData(
            effective_from=since,
            colors_enabled=colors,
            hours_tolerance_pct=Decimal(tolerance),
            hours_red_pct=Decimal(red),
            dates_tolerance_pct=Decimal(tolerance),
            dates_red_pct=Decimal(red),
        )
        return policies.schedule_evaluation_policy(
            self.session, data, today=D(2025, 12, 1), actor_id=None
        )


@pytest.fixture
def world(session: Session) -> World:
    return World(session)


def test_rework_is_derived_from_returned_rounds(session: Session, world: World) -> None:
    report = compare.project_report(session, world.project.id, policy=None)

    assert report.actual.hours == 48
    assert report.actual.rework == 16
    assert report.initial_hours == 32
    assert report.plan is not None
    assert report.plan.hours == 40
    assert report.plan.version.is_baseline
    assert report.rework_by_category == [("Ejecución", Decimal(16))]
    build = next(line for line in report.phases if line.phase and line.phase.id == world.build.id)
    assert (build.planned_hours, build.actual_hours, build.rework_hours) == (40, 40, 16)


def test_a_manual_flag_overrides_the_derived_one(session: Session, world: World) -> None:
    entry = session.query(TimeEntry).filter_by(work_date=D(2026, 7, 15)).one()
    entry.is_rework = False
    session.commit()

    report = compare.project_report(session, world.project.id, policy=None)
    assert report.actual.rework == 8


def test_deviation_is_compared_with_the_tolerance(session: Session, world: World) -> None:
    # 48 h worked against 40 planned: +20 %.
    amber = compare.project_report(
        session, world.project.id, policy=world.policy("10", "25")
    ).evaluation
    assert amber.hours_pct == Decimal("20.00")
    assert amber.hours_status == Compliance.AMBER

    session.rollback()
    red = compare.evaluate(
        world.policy("10", "15", since=D(2026, 2, 1)),
        planned_hours=Decimal(40),
        actual_hours=Decimal(48),
        rework_hours=Decimal(16),
        planned_days=5,
        actual_days=5,
    )
    assert red.hours_status == Compliance.RED
    assert red.dates_status == Compliance.GREEN


def test_underrun_counts_as_deviation_too() -> None:
    assert compare.classify(Decimal("-30"), Decimal(10), Decimal(25)) == Compliance.RED
    assert compare.deviation_pct(None, Decimal(5)) is None
    assert compare.deviation_pct(Decimal(0), Decimal(5)) is None


def test_colours_off_keeps_only_the_figure(session: Session, world: World) -> None:
    policy = world.policy("10", "25", colors=False)
    evaluation = compare.project_report(session, world.project.id, policy=policy).evaluation
    assert evaluation.hours_pct == Decimal("20.00")
    assert evaluation.hours_status is None
    assert not evaluation.colors_enabled


def test_closing_freezes_the_policy_in_force_that_day(session: Session, world: World) -> None:
    world.policy("10", "15")
    snapshot = compare.close_project(session, world.project.id, D(2026, 7, 20), actor_id=None)
    assert snapshot.hours_status == "red"
    assert world.project.closed_on == D(2026, 7, 20)

    # A looser tolerance from August does not rewrite the closed project…
    loose = policies.EvaluationPolicyData(
        effective_from=D(2026, 8, 1),
        hours_tolerance_pct=Decimal(50),
        hours_red_pct=Decimal(60),
        dates_tolerance_pct=Decimal(50),
        dates_red_pct=Decimal(60),
    )
    policies.schedule_evaluation_policy(session, loose, today=D(2026, 7, 25), actor_id=None)
    frozen = compare.summaries(session, today=D(2026, 9, 1))[0]
    assert frozen.evaluation.hours_status == Compliance.RED
    # …but the explicit recalculation uses today's policy.
    assert compare.recalculate(session, world.project.id, D(2026, 9, 1)).hours_status == (
        Compliance.GREEN
    )


def test_closing_twice_or_saving_without_closing_fails(session: Session, world: World) -> None:
    with pytest.raises(compare.ReportError):
        compare.save_snapshot(session, world.project.id, actor_id=None)
    compare.close_project(session, world.project.id, D(2026, 7, 20), actor_id=None)
    with pytest.raises(compare.ReportError):
        compare.close_project(session, world.project.id, D(2026, 7, 21), actor_id=None)
    with pytest.raises(compare.ReportError):
        compare.save_snapshot(session, world.project.id, actor_id=None)


def test_a_project_closed_by_import_can_be_evaluated_later(session: Session, world: World) -> None:
    world.policy("10", "25")
    world.project.closed_on = D(2026, 7, 20)
    session.commit()
    assert compare.summaries(session, today=D(2026, 9, 1))[0].snapshot is None

    compare.save_snapshot(session, world.project.id, actor_id=None)
    assert compare.summaries(session, today=D(2026, 9, 1))[0].snapshot is not None


def test_tickets_caused_by_the_project_are_post_production_rework(
    session: Session, world: World
) -> None:
    ticket = Project(kind=ProjectKind.TICKET.value, code="TK-00001", title="Falla")
    session.add(ticket)
    session.flush()
    session.add(
        WorkItemLink(
            source_id=ticket.id, target_id=world.project.id, link_type=LinkType.CAUSED_BY.value
        )
    )
    create_entry(
        session,
        world.resource.id,
        EntryData(work_date=D(2026, 8, 3), hours=Decimal(3), project_id=ticket.id),
        actor_id=None,
    )
    report = compare.project_report(session, world.project.id, policy=None)
    assert (report.ticket_count, report.ticket_hours) == (1, Decimal(3))


def test_dashboard_by_dimension_and_contingency(session: Session, world: World) -> None:
    bucket = CatalogItem(
        kind="activity_bucket",
        code="INC",
        name="Incidente",
        attributes={"is_contingency": True},
    )
    session.add(bucket)
    session.commit()
    world.log(17, 4, world.build, application_id=world.app.id)
    create_entry(
        session,
        world.resource.id,
        EntryData(work_date=D(2026, 8, 3), hours=Decimal(2), activity_bucket_id=bucket.id),
        actor_id=None,
    )

    by_month = {r.label: r for r in dashboard.hours_by(session, "mes", START, D(2026, 8, 31))}
    assert by_month["2026-07"].hours == 52
    assert by_month["2026-07"].rework == 20
    assert by_month["2026-08"].contingency == 2
    apps = {r.label: r.hours for r in dashboard.hours_by(session, "aplicacion", START, END)}
    assert apps == {"Aplicación A": Decimal(4), "Sin aplicación": Decimal(48)}
    people = dashboard.hours_by(session, "persona", START, END)
    assert [(r.label, r.hours) for r in people] == [("Persona A", Decimal(52))]
    assert dashboard.hours_by(session, "tipo", START, END)[0].label == "Sin tipo de tarea"

    total = dashboard.totals(session, START, D(2026, 8, 31))
    assert (total.hours, total.rework, total.contingency) == (54, 20, 2)
    assert total.rework_share == Decimal("37.0")
    with pytest.raises(ValueError, match="rank"):
        dashboard.hours_by(session, "rank", START, END)


def test_personal_lines_show_only_own_plan_and_hours(session: Session, world: World) -> None:
    other = Resource(display_name="Persona B")
    session.add(other)
    session.commit()
    lines = compare.personal_lines(session, world.resource.id)
    assert [(line.planned_hours, line.actual_hours, line.rework_hours) for line in lines] == [
        (Decimal(40), Decimal(48), Decimal(16))
    ]
    assert compare.personal_lines(session, other.id) == []


def test_notes_need_text_and_are_listed_newest_first(session: Session, world: World) -> None:
    with pytest.raises(compare.ReportError):
        notes.add_note(session, world.project.id, "   ", actor_id=None)
    notes.add_note(session, world.project.id, "Entró un cambio de alcance.", actor_id=None)
    notes.add_note(session, world.project.id, "Ambiente caído dos días.", actor_id=None)
    texts = [n.body for n in notes.project_notes(session, world.project.id)]
    assert texts == ["Ambiente caído dos días.", "Entró un cambio de alcance."]
    assert [n.body for _, n in notes.notes_for_resource(session, world.resource.id)] == texts


# ---------------------------------------------------------------- over the web


def sign_in(session: Session, make_user: MakeUser, login: Login, *roles: Role) -> User:
    user = make_user()
    for role in roles:
        auth.grant_role(session, user, role, actor_id=None)
    session.commit()
    assert login(user.email).status_code == 302
    return user


def test_managers_see_the_reports(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login, world: World
) -> None:
    world.policy("10", "25")
    sign_in(session, make_user, login, Role.LEAD)

    assert "Rework" in client.get("/informes/?desde=2026-07-01&hasta=2026-07-31").get_data(
        as_text=True
    )
    listing = client.get("/informes/proyectos").get_data(as_text=True)
    assert "PJ-00001" in listing
    assert "+20.0 %" in listing
    page = f"/informes/proyectos/{world.project.id}"
    assert "Ejecución" in client.get(page).get_data(as_text=True)

    token = csrf_token(client, page)
    client.post(f"{page}/notas", data={"nota": "Cambio de alcance", "csrf_token": token})
    assert "Cambio de alcance" in client.get(page).get_data(as_text=True)

    client.post(f"{page}/cerrar", data={"cierre": "2026-07-20", "csrf_token": token})
    closed = client.get(f"{page}?recalcular=1").get_data(as_text=True)
    assert "Cerrado el 20/07/2026" in closed
    assert "Recalculado con la política actual" in closed
    assert world.project.closed_on == D(2026, 7, 20)


def test_closing_with_a_bad_date_explains_in_spanish(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login, world: World
) -> None:
    sign_in(session, make_user, login, Role.PM)
    page = f"/informes/proyectos/{world.project.id}"
    token = csrf_token(client, page)
    client.post(f"{page}/cerrar", data={"cierre": "ayer", "csrf_token": token})
    assert "Fecha de cierre: escribe una fecha válida." in client.get(page).get_data(as_text=True)


def test_observers_cannot_see_hours_reports(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login, world: World
) -> None:
    sign_in(session, make_user, login, Role.OBSERVER)
    for path in (
        "/informes/",
        "/informes/proyectos",
        f"/informes/proyectos/{world.project.id}",
        "/informes/mio",
    ):
        assert client.get(path).status_code == 403


def test_developer_sees_only_their_own_report(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login, world: World
) -> None:
    user = sign_in(session, make_user, login, Role.DEVELOPER)
    assert client.get("/informes/").status_code == 403
    assert client.get(f"/informes/proyectos/{world.project.id}").status_code == 403
    token = csrf_token(client, "/informes/mio")
    denied = client.post(
        f"/informes/proyectos/{world.project.id}/notas", data={"nota": "x", "csrf_token": token}
    )
    assert denied.status_code == 403

    # Not linked to a resource yet.
    assert "no está vinculado" in client.get("/informes/mio").get_data(as_text=True)

    world.resource.user_id = user.id
    session.commit()
    notes.add_note(session, world.project.id, "Ambiente caído", actor_id=None)
    page = client.get("/informes/mio?desde=2026-07-01&hasta=2026-07-31").get_data(as_text=True)
    assert "PJ-00001" in page
    assert "Ambiente caído" in page
    assert "48.0" in page
