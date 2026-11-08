from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from flask.testing import FlaskClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.audit.services import MissingReasonError
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.planning import services
from hourplan.planning.models import PlanAllocation, PlanVersion
from hourplan.policies.models import Holiday
from hourplan.projects import services as projects
from hourplan.resources.models import Resource
from hourplan.timesheet.models import Absence, ApprovalStatus
from tests.conftest import csrf_token

MONDAY = date(2026, 7, 6)
FRIDAY = date(2026, 7, 10)
Login = Callable[..., TestResponse]
MakeUser = Callable[..., User]


def setup(session: Session, code: str = "PJ-00001") -> tuple[int, Resource]:
    project = projects.create_project(
        session, projects.ProjectData(code=code, title="Portal"), actor_id=None
    )
    resource = Resource(display_name="Persona A")
    session.add(resource)
    session.commit()
    return project.id, resource


def alloc(resource: Resource, hours: str = "20", start: date = MONDAY, end: date = FRIDAY):
    return services.AllocationData(
        resource_id=resource.id, start_on=start, end_on=end, hours=Decimal(hours)
    )


def test_allocation_rules() -> None:
    with pytest.raises(ValidationError):
        services.AllocationData(resource_id=1, start_on=FRIDAY, end_on=MONDAY, hours=Decimal(8))
    with pytest.raises(ValidationError):
        services.AllocationData(resource_id=1, start_on=MONDAY, end_on=MONDAY, hours=Decimal("1.1"))
    with pytest.raises(ValidationError):
        services.AllocationData(resource_id=1, start_on=MONDAY, end_on=MONDAY, hours=Decimal(0))


def test_first_allocation_creates_version_one(session: Session) -> None:
    project_id, resource = setup(session)

    services.add_allocation(session, project_id, alloc(resource), actor_id=None)
    services.add_allocation(session, project_id, alloc(resource, "4"), actor_id=None)

    version = services.current_version(session, project_id)
    assert version is not None
    assert version.number == 1
    assert len(services.allocations(session, version.id)) == 2


def test_phase_must_belong_to_the_project(session: Session) -> None:
    project_id, resource = setup(session)
    other_id, _ = setup(session, "PJ-00002")
    other = projects.get_project(session, other_id)
    other.phases.append(projects.ProjectPhase(position=1, name="Análisis", phase_type="planning"))
    session.commit()
    data = alloc(resource)
    data.project_phase_id = other.phases[0].id

    with pytest.raises(services.PlanError):
        services.add_allocation(session, project_id, data, actor_id=None)


def test_baseline_is_frozen_until_replanned(session: Session) -> None:
    project_id, resource = setup(session)
    with pytest.raises(services.PlanError):
        services.set_baseline(session, project_id, actor_id=None)
    first = services.add_allocation(session, project_id, alloc(resource), actor_id=None)

    baseline = services.set_baseline(session, project_id, actor_id=None)
    assert baseline.is_baseline
    with pytest.raises(services.FrozenBaselineError):
        services.add_allocation(session, project_id, alloc(resource), actor_id=None)
    with pytest.raises(services.FrozenBaselineError):
        services.remove_allocation(session, project_id, first.id, actor_id=None)
    with pytest.raises(MissingReasonError):
        services.replan(session, project_id, reason="  ", actor_id=None)

    second = services.replan(session, project_id, reason="Cambio de alcance", actor_id=None)
    assert second.number == 2
    assert second.reason == "Cambio de alcance"
    copied = services.allocations(session, second.id)
    assert [a.hours for a in copied] == [Decimal(20)]
    services.remove_allocation(session, project_id, copied[0].id, actor_id=None)
    assert services.allocations(session, baseline.id)
    with pytest.raises(services.PlanError):
        services.set_baseline(session, project_id, actor_id=None)


def test_capacity_subtracts_absences_holidays_and_current_plan(session: Session) -> None:
    project_id, resource = setup(session)
    session.add(Holiday(holiday_date=date(2026, 7, 8), name="Feriado"))
    session.add(
        Absence(
            resource_id=resource.id,
            absence_type="vacation",
            start_on=FRIDAY,
            end_on=FRIDAY,
            status=ApprovalStatus.APPROVED.value,
        )
    )
    session.add(
        Absence(
            resource_id=resource.id,
            absence_type="permission",
            start_on=MONDAY,
            end_on=MONDAY,
            hours_per_day=Decimal(4),
            status=ApprovalStatus.PENDING.value,
        )
    )
    session.commit()
    services.add_allocation(session, project_id, alloc(resource, "20"), actor_id=None)
    services.set_baseline(session, project_id, actor_id=None)
    v2 = services.replan(session, project_id, reason="Más trabajo", actor_id=None)
    services.add_allocation(session, project_id, alloc(resource, "20"), actor_id=None)

    (load,) = services.capacity(session, MONDAY, FRIDAY, [resource.id])
    days = sorted(load.available)

    # Mon, Tue, Thu, Fri are working days; Friday is an approved vacation.
    assert len(days) == 4
    assert load.total("available", days) == Decimal(24)
    assert load.total("absent", days) == Decimal(8)
    # Only the current version (v2: 20 + 20 h) counts, spread over the 4 working days.
    assert load.total("assigned", days) == Decimal(40)
    assert load.free(days) == Decimal(-16)
    assert session.scalar(select(PlanVersion.id).where(PlanVersion.id == v2.id))


def test_bucket_reservations_count_as_assigned(session: Session) -> None:
    _, resource = setup(session)
    from hourplan.catalogs.models import CatalogItem

    bucket = CatalogItem(kind="activity_bucket", code="TICKETS", name="Tickets")
    session.add(bucket)
    session.flush()
    session.add_all(
        [
            PlanAllocation(
                activity_bucket_id=bucket.id,
                resource_id=resource.id,
                start_on=MONDAY,
                end_on=FRIDAY,
                hours=Decimal(10),
            ),
        ]
    )
    session.commit()

    (load,) = services.capacity(session, MONDAY, FRIDAY, [resource.id])
    assert load.total("assigned", sorted(load.available)) == Decimal(10)


def test_weeks_start_on_monday() -> None:
    periods = services.weeks(date(2026, 7, 8), 2)
    assert periods[0] == (MONDAY, date(2026, 7, 12))
    assert periods[1][0] == date(2026, 7, 13)


def sign_in(session: Session, make_user: MakeUser, login: Login, *roles: Role) -> None:
    user = make_user()
    for role in roles:
        auth.grant_role(session, user, role, actor_id=None)
    session.commit()
    assert login(user.email).status_code == 302


def test_plan_over_the_web(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    project_id, resource = setup(session)
    sign_in(session, make_user, login, Role.PM)
    page = f"/planificacion/proyecto/{project_id}"

    def post(path: str, form: dict[str, str]) -> TestResponse:
        return client.post(path, data={**form, "csrf_token": csrf_token(client, page)})

    form = {"persona": str(resource.id), "desde": "2026-07-06", "hasta": "2026-07-10"}
    post(f"{page}/asignaciones", {**form, "horas": "abc"})
    assert "Horas: escribe un número" in client.get(page).get_data(as_text=True)
    assert post(f"{page}/asignaciones", {**form, "horas": "16"}).status_code == 302
    post(f"{page}/linea-base", {})
    html = client.get(page).get_data(as_text=True)
    assert "línea base" in html
    assert "Replanificar" in html
    post(f"{page}/replanificar", {"motivo": ""})
    assert "motivo" in client.get(page).get_data(as_text=True)

    capacity = client.get("/planificacion/capacidad?desde=2026-07-06&semanas=2")
    text = capacity.get_data(as_text=True)
    assert capacity.status_code == 200
    assert "Persona A" in text
    assert "16.0 / 40.0" in text


def test_developer_cannot_plan_or_see_capacity(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    project_id, _ = setup(session)
    sign_in(session, make_user, login, Role.DEVELOPER)

    assert client.get(f"/planificacion/proyecto/{project_id}").status_code == 200
    assert client.get("/planificacion/capacidad").status_code == 403
    token = csrf_token(client, f"/planificacion/proyecto/{project_id}")
    response = client.post(
        f"/planificacion/proyecto/{project_id}/linea-base", data={"csrf_token": token}
    )
    assert response.status_code == 403
