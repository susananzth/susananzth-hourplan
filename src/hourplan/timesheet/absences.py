"""Time off: request, leader decision, cancellation. Absences are never hours worked."""

from datetime import UTC, date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.db import transaction
from hourplan.resources.models import Resource
from hourplan.timesheet.models import Absence, AbsenceType, ApprovalStatus

ENTITY = "absence"


class AbsenceError(ValueError):
    pass


class OverlapError(AbsenceError):
    """The resource already has a pending or approved absence on those days."""


class SelfApprovalError(AbsenceError):
    """Nobody decides their own request."""


class AlreadyDecidedError(AbsenceError):
    pass


class AbsenceData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    absence_type: AbsenceType
    start_on: date
    end_on: date
    # None = full working days.
    hours_per_day: Decimal | None = Field(default=None, gt=0, le=24)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("hours_per_day")
    @classmethod
    def _quarter_hours(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and value % Decimal("0.25") != 0:
            raise ValueError("hours_per_day must be a multiple of 0.25")
        return value

    @field_validator("notes")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def _dates(self) -> AbsenceData:
        if self.end_on < self.start_on:
            raise ValueError("end_on must not be before start_on")
        return self


def _get(session: Session, absence_id: int) -> Absence:
    absence = session.get(Absence, absence_id)
    if absence is None:
        raise AbsenceError("Unknown absence")
    return absence


def _subject(session: Session, absence: Absence) -> int | None:
    resource = session.get(Resource, absence.resource_id)
    return resource.user_id if resource else None


def request_absence(
    session: Session, resource_id: int, data: AbsenceData, *, actor_id: int | None
) -> Absence:
    if session.get(Resource, resource_id) is None:
        raise AbsenceError("Unknown resource")
    overlapping = session.scalar(
        select(Absence.id)
        .where(
            Absence.resource_id == resource_id,
            Absence.status != ApprovalStatus.REJECTED.value,
            Absence.start_on <= data.end_on,
            Absence.end_on >= data.start_on,
        )
        .limit(1)
    )
    if overlapping:
        raise OverlapError("The resource already has an absence on those days")
    with transaction(session):
        absence = Absence(
            resource_id=resource_id,
            absence_type=data.absence_type.value,
            start_on=data.start_on,
            end_on=data.end_on,
            hours_per_day=data.hours_per_day,
            notes=data.notes,
        )
        session.add(absence)
        session.flush()
        audit.record(
            session,
            action=AuditAction.CREATE,
            entity_type=ENTITY,
            entity_id=absence.id,
            actor_id=actor_id,
            changes=data.model_dump(mode="json"),
            subject_user_id=_subject(session, absence),
        )
    return absence


def decide_absence(
    session: Session,
    absence_id: int,
    *,
    approve: bool,
    decider_id: int,
    reason: str | None = None,
) -> Absence:
    """Approve or reject a pending request; rejecting needs a reason the requester can read."""
    absence = _get(session, absence_id)
    if absence.status != ApprovalStatus.PENDING.value:
        raise AlreadyDecidedError("The absence was already decided")
    if _subject(session, absence) == decider_id:
        raise SelfApprovalError("Nobody decides their own absence")
    status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
    with transaction(session):
        audit.record(
            session,
            action=AuditAction.UPDATE,
            entity_type=ENTITY,
            entity_id=absence.id,
            actor_id=decider_id,
            changes={"status": [absence.status, status.value]},
            reason=reason,
            reason_required=not approve,
            subject_user_id=_subject(session, absence),
        )
        absence.status = status.value
        absence.decided_by_id = decider_id
        absence.decided_at = datetime.now(UTC)
    return absence


def cancel_absence(
    session: Session, absence_id: int, *, resource_id: int, today: date, actor_id: int | None
) -> None:
    """The owner withdraws a request, or an approved absence that has not started."""
    absence = _get(session, absence_id)
    if absence.resource_id != resource_id:
        raise AbsenceError("Unknown absence")
    if absence.status == ApprovalStatus.APPROVED.value and absence.start_on <= today:
        raise AbsenceError("An absence that already started cannot be cancelled")
    with transaction(session):
        audit.record(
            session,
            action=AuditAction.DELETE,
            entity_type=ENTITY,
            entity_id=absence.id,
            actor_id=actor_id,
            changes={"status": [absence.status, None]},
            subject_user_id=_subject(session, absence),
        )
        session.delete(absence)


def absences_of(session: Session, resource_id: int, *, since: date) -> list[Absence]:
    return list(
        session.scalars(
            select(Absence)
            .where(Absence.resource_id == resource_id, Absence.end_on >= since)
            .order_by(Absence.start_on)
        )
    )


def pending_requests(session: Session) -> list[tuple[Absence, str]]:
    """Requests waiting for a decision, with the requester's name."""
    rows = session.execute(
        select(Absence, Resource.display_name)
        .join(Resource, Resource.id == Absence.resource_id)
        .where(Absence.status == ApprovalStatus.PENDING.value)
        .order_by(Absence.start_on)
    )
    return [(absence, name) for absence, name in rows]
