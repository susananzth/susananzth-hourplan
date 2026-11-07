"""Time bank: hours above the working day and unplanned contingencies accumulate; a day off
can be paid with them. Every movement waits for a leader's approval; only approved ones count.

A day's contingency hours are claimed on their own, and the overtime claim of that day
excludes them, so the same hour is never banked twice.
"""

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.catalogs.models import CatalogItem
from hourplan.db import transaction
from hourplan.resources.models import Resource
from hourplan.timesheet import services
from hourplan.timesheet.models import (
    Absence,
    AbsenceType,
    ApprovalStatus,
    BankReason,
    OvertimeKind,
    TimeBankLedger,
    TimeEntry,
)

ENTITY = "time_bank"


class BankError(ValueError):
    pass


class NothingToClaimError(BankError):
    pass


class AlreadyClaimedError(BankError):
    pass


class InsufficientBankError(BankError):
    pass


class SelfApprovalError(BankError):
    pass


class AlreadyDecidedError(BankError):
    pass


def _resource(session: Session, resource_id: int) -> Resource:
    resource = session.get(Resource, resource_id)
    if resource is None:
        raise BankError("No se encontró el recurso.")
    return resource


def balance(session: Session, resource_id: int) -> Decimal:
    """Approved hours in the bank."""
    total = session.scalar(
        select(func.coalesce(func.sum(TimeBankLedger.hours), 0)).where(
            TimeBankLedger.resource_id == resource_id,
            TimeBankLedger.status == ApprovalStatus.APPROVED.value,
        )
    )
    return Decimal(total or 0)


def pending_hours(session: Session, resource_id: int) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(TimeBankLedger.hours), 0)).where(
            TimeBankLedger.resource_id == resource_id,
            TimeBankLedger.status == ApprovalStatus.PENDING.value,
        )
    )
    return Decimal(total or 0)


def _claimed(session: Session, resource_id: int, day: date, reason: BankReason) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(TimeBankLedger.hours), 0)).where(
            TimeBankLedger.resource_id == resource_id,
            TimeBankLedger.entry_date == day,
            TimeBankLedger.reason == reason.value,
            TimeBankLedger.status != ApprovalStatus.REJECTED.value,
        )
    )
    return Decimal(total or 0)


def contingency_hours(session: Session, resource_id: int, day: date) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(TimeEntry.hours), 0))
        .join(CatalogItem, CatalogItem.id == TimeEntry.activity_bucket_id)
        .where(
            TimeEntry.resource_id == resource_id,
            TimeEntry.work_date == day,
            CatalogItem.attributes["is_contingency"].as_boolean().is_(True),
        )
    )
    return Decimal(total or 0)


def _add(
    session: Session,
    resource: Resource,
    day: date,
    hours: Decimal,
    reason: BankReason,
    *,
    kind: OvertimeKind | None = None,
    notes: str | None = None,
    actor_id: int | None,
) -> TimeBankLedger:
    with transaction(session):
        movement = TimeBankLedger(
            resource_id=resource.id,
            entry_date=day,
            hours=hours,
            reason=reason.value,
            overtime_kind=kind.value if kind else None,
            notes=(notes or "").strip() or None,
            created_by_id=actor_id,
        )
        session.add(movement)
        session.flush()
        audit.record(
            session,
            action=AuditAction.CREATE,
            entity_type=ENTITY,
            entity_id=movement.id,
            actor_id=actor_id,
            changes={"entry_date": day, "hours": hours, "reason": reason.value},
            subject_user_id=resource.user_id,
        )
    return movement


def claim_overtime(
    session: Session,
    resource_id: int,
    day: date,
    kind: OvertimeKind,
    *,
    notes: str | None = None,
    actor_id: int | None,
) -> TimeBankLedger:
    """Ask to bank the hours above the working day (voluntary or required by the leader)."""
    resource = _resource(session, resource_id)
    if _claimed(session, resource_id, day, BankReason.OVERTIME):
        raise AlreadyClaimedError("Las horas extra de ese día ya fueron reclamadas.")
    summary = services.day_summary(session, resource, day)
    extra = summary.overtime - _claimed(session, resource_id, day, BankReason.CONTINGENCY)
    if extra <= 0:
        raise NothingToClaimError("Ese día no tiene horas sobre la jornada para reclamar.")
    return _add(
        session,
        resource,
        day,
        extra,
        BankReason.OVERTIME,
        kind=kind,
        notes=notes,
        actor_id=actor_id,
    )


def claim_contingency(
    session: Session, resource_id: int, day: date, *, actor_id: int | None
) -> TimeBankLedger:
    resource = _resource(session, resource_id)
    if _claimed(session, resource_id, day, BankReason.CONTINGENCY):
        raise AlreadyClaimedError("La contingencia de ese día ya fue reclamada.")
    hours = contingency_hours(session, resource_id, day)
    if hours <= 0:
        raise NothingToClaimError("Ese día no tiene horas de contingencia para reclamar.")
    return _add(session, resource, day, hours, BankReason.CONTINGENCY, actor_id=actor_id)


def decide(
    session: Session,
    movement_id: int,
    *,
    approve: bool,
    decider_id: int,
    reason: str | None = None,
) -> TimeBankLedger:
    movement = session.get(TimeBankLedger, movement_id)
    if movement is None:
        raise BankError("No se encontró ese movimiento.")
    if movement.status != ApprovalStatus.PENDING.value:
        raise AlreadyDecidedError("Ese movimiento ya fue resuelto.")
    resource = _resource(session, movement.resource_id)
    if resource.user_id == decider_id:
        raise SelfApprovalError("No puedes decidir tus propios movimientos.")
    status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    with transaction(session):
        audit.record(
            session,
            action=AuditAction.UPDATE,
            entity_type=ENTITY,
            entity_id=movement.id,
            actor_id=decider_id,
            changes={"status": [movement.status, status.value]},
            reason=reason,
            reason_required=not approve,
            subject_user_id=resource.user_id,
        )
        movement.status = status.value
        movement.decided_by_id = decider_id
        movement.decided_at = datetime.now(UTC)
    return movement


def compensation_hours(session: Session, absence: Absence) -> Decimal:
    """Hours a compensation absence costs: its working days times the hours of each day."""
    resource = _resource(session, absence.resource_id)
    total = Decimal(0)
    for day in services.working_days(session, absence.start_on, absence.end_on):
        standard = services.day_summary(session, resource, day).standard
        total += absence.hours_per_day or standard
    return total


def charge_compensation(session: Session, absence: Absence, *, decider_id: int) -> None:
    """Take the hours of an approved compensation absence out of the bank."""
    if absence.absence_type != AbsenceType.COMPENSATION.value:
        return
    hours = compensation_hours(session, absence)
    if hours <= 0:
        return
    if balance(session, absence.resource_id) < hours:
        raise InsufficientBankError("El banco de horas no cubre ese día compensado.")
    session.add(
        TimeBankLedger(
            resource_id=absence.resource_id,
            entry_date=absence.start_on,
            hours=-hours,
            reason=BankReason.COMPENSATION.value,
            status=ApprovalStatus.APPROVED.value,
            absence_id=absence.id,
            decided_by_id=decider_id,
            decided_at=datetime.now(UTC),
            created_by_id=decider_id,
        )
    )


def refund_compensation(session: Session, absence: Absence) -> None:
    """Give the hours back when a compensation absence is cancelled."""
    for movement in session.scalars(
        select(TimeBankLedger).where(TimeBankLedger.absence_id == absence.id)
    ):
        session.delete(movement)


def movements(session: Session, resource_id: int, *, limit: int = 50) -> list[TimeBankLedger]:
    return list(
        session.scalars(
            select(TimeBankLedger)
            .where(TimeBankLedger.resource_id == resource_id)
            .order_by(TimeBankLedger.entry_date.desc(), TimeBankLedger.id.desc())
            .limit(limit)
        )
    )


def pending_movements(session: Session) -> list[tuple[TimeBankLedger, str]]:
    rows = session.execute(
        select(TimeBankLedger, Resource.display_name)
        .join(Resource, Resource.id == TimeBankLedger.resource_id)
        .where(TimeBankLedger.status == ApprovalStatus.PENDING.value)
        .order_by(TimeBankLedger.entry_date)
    )
    return [(movement, name) for movement, name in rows]
