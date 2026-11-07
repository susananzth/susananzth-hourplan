from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from flask.testing import FlaskClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.audit.models import AuditLog
from hourplan.audit.services import MissingReasonError
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.policies import services as policies
from hourplan.policies.models import Holiday
from hourplan.resources.models import Resource
from hourplan.timesheet import absences, services
from hourplan.timesheet.models import Absence, AbsenceType, ApprovalStatus
from tests.conftest import csrf_token

DAY = date(2026, 7, 6)
Login = Callable[..., TestResponse]
MakeUser = Callable[..., User]


def data(start: date = DAY, end: date = DAY, hours: str | None = None) -> absences.AbsenceData:
    return absences.AbsenceData(
        absence_type=AbsenceType.VACATION,
        start_on=start,
        end_on=end,
        hours_per_day=Decimal(hours) if hours else None,
    )


def person(session: Session, make_user: MakeUser, *roles: Role) -> tuple[User, Resource]:
    user = make_user()
    for role in roles:
        auth.grant_role(session, user, role, actor_id=None)
    resource = Resource(display_name=f"Persona {user.id}", user_id=user.id)
    session.add(resource)
    session.commit()
    return user, resource


def test_data_rules() -> None:
    with pytest.raises(ValidationError):
        data(DAY, date(2026, 7, 5))
    with pytest.raises(ValidationError):
        data(hours="0.1")


def test_request_is_pending_and_overlaps_are_refused(session: Session, make_user: MakeUser) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)

    absence = absences.request_absence(session, resource.id, data(), actor_id=None)
    assert absence.status == ApprovalStatus.PENDING.value
    with pytest.raises(absences.OverlapError):
        absences.request_absence(session, resource.id, data(DAY, date(2026, 7, 8)), actor_id=None)


def test_rejected_absence_frees_the_days(session: Session, make_user: MakeUser) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)
    lead, _ = person(session, make_user, Role.LEAD)
    first = absences.request_absence(session, resource.id, data(), actor_id=None)
    absences.decide_absence(session, first.id, approve=False, decider_id=lead.id, reason="Cierre")

    assert absences.request_absence(session, resource.id, data(), actor_id=None).id != first.id


def test_decisions_need_a_reason_to_reject_and_cannot_be_self_service(
    session: Session, make_user: MakeUser
) -> None:
    user, resource = person(session, make_user, Role.LEAD)
    lead, _ = person(session, make_user, Role.LEAD)
    absence = absences.request_absence(session, resource.id, data(), actor_id=user.id)

    with pytest.raises(absences.SelfApprovalError):
        absences.decide_absence(session, absence.id, approve=True, decider_id=user.id)
    with pytest.raises(MissingReasonError):
        absences.decide_absence(session, absence.id, approve=False, decider_id=lead.id)
    session.rollback()

    decided = absences.decide_absence(session, absence.id, approve=True, decider_id=lead.id)
    assert decided.status == ApprovalStatus.APPROVED.value
    assert decided.decided_by_id == lead.id
    with pytest.raises(absences.AlreadyDecidedError):
        absences.decide_absence(session, absence.id, approve=True, decider_id=lead.id)
    actions = session.scalars(select(AuditLog.action).where(AuditLog.entity_type == "absence"))
    assert sorted(actions) == ["create", "update"]


def test_approved_absence_reduces_the_working_day(session: Session, make_user: MakeUser) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)
    lead, _ = person(session, make_user, Role.LEAD)
    pending = absences.request_absence(session, resource.id, data(hours="4"), actor_id=None)
    assert services.day_summary(session, resource, DAY).absence == 0

    absences.decide_absence(session, pending.id, approve=True, decider_id=lead.id)

    assert services.day_summary(session, resource, DAY).absence == Decimal(4)


def test_cancel_rules(session: Session, make_user: MakeUser) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)
    _, other = person(session, make_user, Role.DEVELOPER)
    lead, _ = person(session, make_user, Role.LEAD)
    absence = absences.request_absence(session, resource.id, data(), actor_id=None)

    with pytest.raises(absences.AbsenceError):
        absences.cancel_absence(session, absence.id, resource_id=other.id, today=DAY, actor_id=None)
    absences.decide_absence(session, absence.id, approve=True, decider_id=lead.id)
    with pytest.raises(absences.AbsenceError):
        absences.cancel_absence(
            session, absence.id, resource_id=resource.id, today=DAY, actor_id=None
        )
    absences.cancel_absence(
        session, absence.id, resource_id=resource.id, today=date(2026, 7, 1), actor_id=None
    )
    assert session.get(Absence, absence.id) is None


def sign_in(session: Session, make_user: MakeUser, login: Login, *roles: Role) -> Resource:
    user, resource = person(session, make_user, *roles)
    assert login(user.email).status_code == 302
    return resource


def post(client: FlaskClient, path: str, form: dict[str, str], page: str = "/ausencias"):
    return client.post(path, data={**form, "csrf_token": csrf_token(client, page)})


def test_request_over_the_web_and_lead_decides(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    resource = sign_in(session, make_user, login, Role.DEVELOPER)
    form = {"tipo": "vacation", "desde": "2027-07-05", "hasta": "2027-07-06", "horas": ""}

    assert post(client, "/ausencias", form).status_code == 302
    assert "Pendiente" in client.get("/ausencias").get_data(as_text=True)
    assert post(client, "/ausencias", form).status_code == 302
    assert "ya tienes una ausencia" in client.get("/ausencias").get_data(as_text=True).lower()
    absence = session.scalars(select(Absence).where(Absence.resource_id == resource.id)).one()

    # A developer cannot decide.
    assert (
        post(client, f"/ausencias/{absence.id}/decidir", {"decision": "approve"}).status_code == 403
    )


def test_invalid_form_is_reported(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.DEVELOPER)
    form = {"tipo": "vacation", "desde": "2026-07-07", "hasta": "2026-07-06", "horas": "x"}

    response = post(client, "/ausencias", form)

    assert response.status_code == 302
    assert "Revisa las fechas" in client.get("/ausencias").get_data(as_text=True)


def test_lead_sees_and_decides_pending_requests(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    _, requester = person(session, make_user, Role.DEVELOPER)
    absence = absences.request_absence(session, requester.id, data(), actor_id=None)
    sign_in(session, make_user, login, Role.LEAD)

    assert "Solicitudes por resolver" in client.get("/ausencias").get_data(as_text=True)
    post(client, f"/ausencias/{absence.id}/decidir", {"decision": "reject", "motivo": ""})
    assert "escribe un motivo" in client.get("/ausencias").get_data(as_text=True)
    post(client, f"/ausencias/{absence.id}/decidir", {"decision": "approve"})
    session.refresh(absence)
    assert absence.status == ApprovalStatus.APPROVED.value


def test_user_without_resource_and_anonymous(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    assert client.get("/ausencias").status_code == 302
    user = make_user()
    auth.grant_role(session, user, Role.DEVELOPER, actor_id=None)
    login(user.email)

    assert "no está vinculado" in client.get("/ausencias").get_data(as_text=True)


def test_holiday_services(session: Session) -> None:
    holiday = policies.add_holiday(session, date(2026, 9, 18), " Fiestas Patrias ", actor_id=None)
    assert holiday.name == "Fiestas Patrias"
    with pytest.raises(policies.HolidayError):
        policies.add_holiday(session, date(2026, 9, 18), "Otro", actor_id=None)
    with pytest.raises(policies.HolidayError):
        policies.add_holiday(session, date(2026, 9, 19), "  ", actor_id=None)

    assert [h.name for h in policies.list_holidays(session, 2026)] == ["Fiestas Patrias"]
    policies.delete_holiday(session, holiday.id, actor_id=None)
    assert policies.list_holidays(session, 2026) == []
    with pytest.raises(policies.HolidayError):
        policies.delete_holiday(session, holiday.id, actor_id=None)


def test_holiday_admin_screens(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.ADMIN)

    post(client, "/admin/feriados", {"fecha": "2026-09-18", "nombre": "Fiestas"}, "/admin/feriados")
    assert "Fiestas" in client.get("/admin/feriados?anio=2026").get_data(as_text=True)
    post(client, "/admin/feriados", {"fecha": "2026-09-18", "nombre": "Dup"}, "/admin/feriados")
    assert "no se repite" in client.get("/admin/feriados").get_data(as_text=True)
    post(client, "/admin/feriados", {"fecha": "mala", "nombre": "X"}, "/admin/feriados")
    holiday = session.scalars(select(Holiday)).one()
    post(client, f"/admin/feriados/{holiday.id}/borrar", {}, "/admin/feriados")
    assert session.scalars(select(Holiday)).first() is None
    assert client.get("/admin/feriados?anio=1").status_code == 400


def test_holiday_screens_are_admin_only(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    sign_in(session, make_user, login, Role.PM)

    assert client.get("/admin/feriados").status_code == 403
