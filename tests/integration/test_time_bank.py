from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from flask.testing import FlaskClient
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.audit.services import MissingReasonError
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.timesheet import absences, bank, services
from hourplan.timesheet.models import (
    AbsenceType,
    ApprovalStatus,
    BankReason,
    OvertimeKind,
    TimeBankLedger,
)
from tests.conftest import csrf_token

DAY = date(2026, 7, 6)  # Monday
Login = Callable[..., TestResponse]
MakeUser = Callable[..., User]


@pytest.fixture
def project(session: Session) -> Project:
    item = Project(code="PJ-00001", title="Portal")
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def incident(session: Session) -> CatalogItem:
    item = CatalogItem(
        kind=CatalogKind.ACTIVITY_BUCKET.value,
        code="INC",
        name="Incidente",
        attributes={"is_contingency": True},
    )
    session.add(item)
    session.commit()
    return item


def person(session: Session, make_user: MakeUser, *roles: Role) -> tuple[User, Resource]:
    user = make_user()
    for role in roles:
        auth.grant_role(session, user, role, actor_id=None)
    resource = Resource(display_name=f"Persona {user.id}", user_id=user.id)
    session.add(resource)
    session.commit()
    return user, resource


def log(session: Session, resource: Resource, hours: str, **target: int) -> None:
    data = services.EntryData(work_date=DAY, hours=Decimal(hours), **target)
    services.create_entry(session, resource.id, data, actor_id=None)


def approve(session: Session, make_user: MakeUser, movement: TimeBankLedger) -> None:
    lead, _ = person(session, make_user, Role.LEAD)
    bank.decide(session, movement.id, approve=True, decider_id=lead.id)


def test_overtime_claim_waits_for_approval(
    session: Session, make_user: MakeUser, project: Project
) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)
    log(session, resource, "10.5", project_id=project.id)

    movement = bank.claim_overtime(session, resource.id, DAY, OvertimeKind.VOLUNTARY, actor_id=None)

    assert movement.hours == Decimal("2.5")
    assert (bank.balance(session, resource.id), bank.pending_hours(session, resource.id)) == (
        Decimal(0),
        Decimal("2.5"),
    )
    approve(session, make_user, movement)
    assert bank.balance(session, resource.id) == Decimal("2.5")
    with pytest.raises(bank.AlreadyClaimedError):
        bank.claim_overtime(session, resource.id, DAY, OvertimeKind.REQUIRED, actor_id=None)


def test_nothing_to_claim_on_a_normal_day(
    session: Session, make_user: MakeUser, project: Project
) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)
    log(session, resource, "8", project_id=project.id)

    with pytest.raises(bank.NothingToClaimError):
        bank.claim_overtime(session, resource.id, DAY, OvertimeKind.VOLUNTARY, actor_id=None)
    with pytest.raises(bank.NothingToClaimError):
        bank.claim_contingency(session, resource.id, DAY, actor_id=None)


def test_contingency_is_not_banked_twice(
    session: Session, make_user: MakeUser, project: Project, incident: CatalogItem
) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)
    log(session, resource, "8", project_id=project.id)
    log(session, resource, "3", activity_bucket_id=incident.id)

    contingency = bank.claim_contingency(session, resource.id, DAY, actor_id=None)

    assert contingency.hours == Decimal(3)
    # 11 h worked: the 3 h above the day are all banked already as contingency.
    with pytest.raises(bank.NothingToClaimError):
        bank.claim_overtime(session, resource.id, DAY, OvertimeKind.REQUIRED, actor_id=None)
    log(session, resource, "1", project_id=project.id)
    extra = bank.claim_overtime(session, resource.id, DAY, OvertimeKind.REQUIRED, actor_id=None)
    assert extra.hours == Decimal(1)
    assert extra.overtime_kind == OvertimeKind.REQUIRED.value


def test_decisions(session: Session, make_user: MakeUser, project: Project) -> None:
    user, resource = person(session, make_user, Role.LEAD)
    lead, _ = person(session, make_user, Role.LEAD)
    log(session, resource, "9", project_id=project.id)
    movement = bank.claim_overtime(session, resource.id, DAY, OvertimeKind.VOLUNTARY, actor_id=None)

    with pytest.raises(bank.SelfApprovalError):
        bank.decide(session, movement.id, approve=True, decider_id=user.id)
    with pytest.raises(MissingReasonError):
        bank.decide(session, movement.id, approve=False, decider_id=lead.id)
    session.rollback()
    bank.decide(session, movement.id, approve=False, decider_id=lead.id, reason="No aplica")
    assert movement.status == ApprovalStatus.REJECTED.value
    assert bank.balance(session, resource.id) == 0
    with pytest.raises(bank.AlreadyDecidedError):
        bank.decide(session, movement.id, approve=True, decider_id=lead.id)
    with pytest.raises(bank.BankError):
        bank.decide(session, 999_999, approve=True, decider_id=lead.id)
    # A rejected claim can be made again.
    bank.claim_overtime(session, resource.id, DAY, OvertimeKind.VOLUNTARY, actor_id=None)


def compensation(resource: Resource, start: date, end: date) -> absences.AbsenceData:
    return absences.AbsenceData(absence_type=AbsenceType.COMPENSATION, start_on=start, end_on=end)


def fund(session: Session, resource: Resource, hours: str) -> None:
    session.add(
        TimeBankLedger(
            resource_id=resource.id,
            entry_date=DAY,
            hours=Decimal(hours),
            reason=BankReason.ADJUSTMENT.value,
            status=ApprovalStatus.APPROVED.value,
        )
    )
    session.commit()


def test_compensation_day_spends_the_bank_and_cancelling_refunds_it(
    session: Session, make_user: MakeUser
) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)
    lead, _ = person(session, make_user, Role.LEAD)
    fund(session, resource, "20")
    # Friday to Monday: two working days.
    request = absences.request_absence(
        session,
        resource.id,
        compensation(resource, date(2026, 8, 7), date(2026, 8, 10)),
        actor_id=None,
    )

    absences.decide_absence(session, request.id, approve=True, decider_id=lead.id)

    assert bank.balance(session, resource.id) == Decimal(4)
    absences.cancel_absence(
        session, request.id, resource_id=resource.id, today=date(2026, 8, 1), actor_id=None
    )
    assert bank.balance(session, resource.id) == Decimal(20)


def test_compensation_needs_enough_bank(session: Session, make_user: MakeUser) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)
    lead, _ = person(session, make_user, Role.LEAD)
    fund(session, resource, "4")
    request = absences.request_absence(
        session, resource.id, compensation(resource, DAY, DAY), actor_id=None
    )

    with pytest.raises(bank.InsufficientBankError):
        absences.decide_absence(session, request.id, approve=True, decider_id=lead.id)
    session.rollback()
    session.refresh(request)
    assert request.status == ApprovalStatus.PENDING.value
    assert bank.balance(session, resource.id) == Decimal(4)


def test_other_absences_do_not_touch_the_bank(session: Session, make_user: MakeUser) -> None:
    _, resource = person(session, make_user, Role.DEVELOPER)
    lead, _ = person(session, make_user, Role.LEAD)
    vacation = absences.AbsenceData(absence_type=AbsenceType.VACATION, start_on=DAY, end_on=DAY)
    request = absences.request_absence(session, resource.id, vacation, actor_id=None)

    absences.decide_absence(session, request.id, approve=True, decider_id=lead.id)

    assert session.scalars(select(TimeBankLedger)).first() is None


def sign_in(session: Session, make_user: MakeUser, login: Login, *roles: Role) -> Resource:
    user, resource = person(session, make_user, *roles)
    assert login(user.email).status_code == 302
    return resource


def post(client: FlaskClient, path: str, form: dict[str, str]):
    return client.post(path, data={**form, "csrf_token": csrf_token(client, "/banco")})


def test_bank_screen_claim_and_decision(
    client: FlaskClient,
    session: Session,
    make_user: MakeUser,
    login: Login,
    project: Project,
) -> None:
    resource = sign_in(session, make_user, login, Role.LEAD)
    log(session, resource, "9", project_id=project.id)
    _, other = person(session, make_user, Role.DEVELOPER)
    log(session, other, "10", project_id=project.id)
    waiting = bank.claim_overtime(session, other.id, DAY, OvertimeKind.REQUIRED, actor_id=None)

    claim = {"dia": DAY.isoformat(), "motivo": "overtime", "tipo": "voluntary"}
    assert post(client, "/banco/reclamar", claim).status_code == 302
    html = client.get("/banco").get_data(as_text=True)
    assert "Solicitud enviada" in html
    assert "Movimientos por resolver" in html

    post(client, "/banco/reclamar", claim)
    assert "ya fueron reclamadas" in client.get("/banco").get_data(as_text=True)
    post(client, "/banco/reclamar", {**claim, "motivo": "contingency"})
    assert "no tiene horas de contingencia" in client.get("/banco").get_data(as_text=True)

    post(client, f"/banco/{waiting.id}/decidir", {"decision": "reject", "motivo_rechazo": ""})
    assert "escribe un motivo" in client.get("/banco").get_data(as_text=True)
    post(client, f"/banco/{waiting.id}/decidir", {"decision": "approve"})
    assert bank.balance(session, other.id) == Decimal(2)


def test_bank_screen_permissions(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    assert client.get("/banco").status_code == 302
    sign_in(session, make_user, login, Role.DEVELOPER)

    assert client.get("/banco").status_code == 200
    assert post(client, "/banco/1/decidir", {"decision": "approve"}).status_code == 403
    assert post(client, "/banco/reclamar", {"dia": "mala"}).status_code == 400
    bad_kind = {"dia": DAY.isoformat(), "motivo": "overtime", "tipo": "x"}
    assert post(client, "/banco/reclamar", bad_kind).status_code == 400
