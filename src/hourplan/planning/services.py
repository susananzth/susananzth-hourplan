"""Project plans (versions, baseline, replanning) and resource capacity.

The current plan of a project is its latest version. The baseline is frozen: once the latest
version is the baseline, changing the plan means replanning, which copies it into a new version
with a reason. Capacity = working hours - approved absences - allocated hours.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.catalogs.models import Application
from hourplan.db import transaction
from hourplan.planning.models import PlanAllocation, PlanVersion
from hourplan.policies import services as policies
from hourplan.policies.models import WorkPolicy
from hourplan.projects.models import Project, ProjectPhase
from hourplan.resources.models import Resource
from hourplan.shared.labels import label
from hourplan.timesheet.models import Absence, ApprovalStatus
from hourplan.timesheet.services import working_days

VERSION_ENTITY = "plan_version"
ALLOCATION_ENTITY = "plan_allocation"
DEFAULT_DAILY_HOURS = Decimal(8)
_COPIED = ("resource_id", "project_phase_id", "application_id", "start_on", "end_on", "hours")


class PlanError(ValueError):
    pass


class FrozenBaselineError(PlanError):
    """The latest version is the baseline: replan before changing it."""


class AllocationData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: int
    project_phase_id: int | None = None
    application_id: int | None = None
    start_on: date
    end_on: date
    hours: Decimal = Field(gt=0, le=10000)

    @field_validator("hours")
    @classmethod
    def _quarter_hours(cls, value: Decimal) -> Decimal:
        if value % Decimal("0.25") != 0:
            raise ValueError(f"{label('hours')} debe ser múltiplo de 0,25.")
        return value

    @model_validator(mode="after")
    def _dates(self) -> AllocationData:
        if self.end_on < self.start_on:
            raise ValueError(
                f"{label('end_on')} no puede ser anterior a {label('start_on').lower()}."
            )
        return self


# ---------------------------------------------------------------- versions


def versions(session: Session, project_id: int) -> list[PlanVersion]:
    return list(
        session.scalars(
            select(PlanVersion)
            .where(PlanVersion.project_id == project_id)
            .order_by(PlanVersion.number.desc())
        )
    )


def current_version(session: Session, project_id: int) -> PlanVersion | None:
    return session.scalars(
        select(PlanVersion)
        .where(PlanVersion.project_id == project_id)
        .order_by(PlanVersion.number.desc())
        .limit(1)
    ).first()


def allocations(session: Session, version_id: int) -> list[PlanAllocation]:
    return list(
        session.scalars(
            select(PlanAllocation)
            .where(PlanAllocation.plan_version_id == version_id)
            .order_by(PlanAllocation.start_on, PlanAllocation.id)
        )
    )


def plan_span(session: Session, version_id: int) -> tuple[date, date] | None:
    row = session.execute(
        select(func.min(PlanAllocation.start_on), func.max(PlanAllocation.end_on)).where(
            PlanAllocation.plan_version_id == version_id
        )
    ).one()
    return (row[0], row[1]) if row[0] else None


def _project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise PlanError("No se encontró ese proyecto.")
    return project


def _new_version(
    session: Session,
    project_id: int,
    number: int,
    *,
    label_text: str,
    reason: str | None,
    actor_id: int | None,
) -> PlanVersion:
    version = PlanVersion(
        project_id=project_id,
        number=number,
        label=label_text,
        reason=reason,
        created_by_id=actor_id,
    )
    session.add(version)
    session.flush()
    audit.record(
        session,
        action=AuditAction.CREATE,
        entity_type=VERSION_ENTITY,
        entity_id=version.id,
        actor_id=actor_id,
        changes={"project_id": project_id, "number": number},
        reason=reason,
    )
    return version


def _editable_version(session: Session, project_id: int, actor_id: int | None) -> PlanVersion:
    version = current_version(session, project_id)
    if version is None:
        return _new_version(
            session, project_id, 1, label_text="Plan inicial", reason=None, actor_id=actor_id
        )
    if version.is_baseline:
        raise FrozenBaselineError(
            "La línea base no se modifica. Crea una replanificación para cambiar el plan."
        )
    return version


def set_baseline(session: Session, project_id: int, *, actor_id: int | None) -> PlanVersion:
    """Freeze the current version as the baseline. A project has a single baseline."""
    _project(session, project_id)
    version = current_version(session, project_id)
    if version is None or not allocations(session, version.id):
        raise PlanError("Asigna horas antes de fijar la línea base.")
    has_baseline = session.scalar(
        select(PlanVersion.id).where(PlanVersion.project_id == project_id, PlanVersion.is_baseline)
    )
    if has_baseline:
        raise PlanError("Este proyecto ya tiene línea base; los cambios van en replanificaciones.")
    with transaction(session):
        version.is_baseline = True
        audit.record(
            session,
            action=AuditAction.UPDATE,
            entity_type=VERSION_ENTITY,
            entity_id=version.id,
            actor_id=actor_id,
            changes={"is_baseline": [False, True]},
        )
    return version


def replan(
    session: Session, project_id: int, *, reason: str | None, actor_id: int | None
) -> PlanVersion:
    """Copy the current version into a new one. Every replanning needs a reason."""
    _project(session, project_id)
    reason = (reason or "").strip()
    if not reason:
        raise audit.MissingReasonError("Escribe el motivo de la replanificación.")
    latest = current_version(session, project_id)
    if latest is None:
        raise PlanError("El proyecto aún no tiene plan que replanificar.")
    with transaction(session):
        version = _new_version(
            session,
            project_id,
            latest.number + 1,
            label_text=f"Replanificación {latest.number}",
            reason=reason[:1000],
            actor_id=actor_id,
        )
        for old in allocations(session, latest.id):
            session.add(
                PlanAllocation(
                    plan_version_id=version.id,
                    **{name: getattr(old, name) for name in _COPIED},
                )
            )
    return version


# ---------------------------------------------------------------- allocations


def _check_allocation(session: Session, project_id: int, data: AllocationData) -> None:
    resource = session.get(Resource, data.resource_id)
    if resource is None or not resource.is_active:
        raise PlanError(f"{label('resource_id')}: no se encontró esa persona.")
    if data.project_phase_id is not None:
        phase = session.get(ProjectPhase, data.project_phase_id)
        if phase is None or phase.project_id != project_id:
            raise PlanError(f"{label('project_phase_id')}: esa fase no es de este proyecto.")
    if data.application_id is not None and session.get(Application, data.application_id) is None:
        raise PlanError(f"{label('application_id')}: no se encontró esa aplicación.")


def add_allocation(
    session: Session, project_id: int, data: AllocationData, *, actor_id: int | None
) -> PlanAllocation:
    _project(session, project_id)
    _check_allocation(session, project_id, data)
    with transaction(session):
        version = _editable_version(session, project_id, actor_id)
        allocation = PlanAllocation(plan_version_id=version.id, **data.model_dump())
        session.add(allocation)
        session.flush()
        audit.record(
            session,
            action=AuditAction.CREATE,
            entity_type=ALLOCATION_ENTITY,
            entity_id=allocation.id,
            actor_id=actor_id,
            changes={"plan_version_id": version.id, **data.model_dump(mode="json")},
        )
    return allocation


def remove_allocation(
    session: Session, project_id: int, allocation_id: int, *, actor_id: int | None
) -> None:
    allocation = session.get(PlanAllocation, allocation_id)
    version = current_version(session, project_id)
    if allocation is None or version is None or allocation.plan_version_id != version.id:
        raise PlanError("No se encontró esa asignación en el plan vigente.")
    if version.is_baseline:
        raise FrozenBaselineError(
            "La línea base no se modifica. Crea una replanificación para cambiar el plan."
        )
    with transaction(session):
        audit.record(
            session,
            action=AuditAction.DELETE,
            entity_type=ALLOCATION_ENTITY,
            entity_id=allocation.id,
            actor_id=actor_id,
            changes={name: audit.to_json(getattr(allocation, name)) for name in _COPIED},
        )
        session.delete(allocation)


# ---------------------------------------------------------------- capacity


@dataclass
class ResourceLoad:
    """Hours per day of one resource over a period."""

    resource: Resource
    available: dict[date, Decimal] = field(default_factory=dict)
    absent: dict[date, Decimal] = field(default_factory=dict)
    assigned: dict[date, Decimal] = field(default_factory=lambda: defaultdict(Decimal))

    def total(self, kind: str, days: list[date]) -> Decimal:
        values: dict[date, Decimal] = getattr(self, kind)
        return sum((values.get(d, Decimal(0)) for d in days), Decimal(0))

    def free(self, days: list[date]) -> Decimal:
        return self.total("available", days) - self.total("assigned", days)


def _standard_hours(session: Session, start: date, end: date) -> dict[date, Decimal]:
    """Standard daily hours on each day, following the policy in force that day."""
    history = sorted(
        policies.list_policies(session, WorkPolicy), key=lambda p: p.effective_from, reverse=True
    )
    result: dict[date, Decimal] = {}
    day = start
    while day <= end:
        policy = next((p for p in history if p.effective_from <= day), None)
        result[day] = policy.standard_daily_hours if policy else DEFAULT_DAILY_HOURS
        day += timedelta(days=1)
    return result


def current_version_ids() -> Select[int]:
    """Ids of the latest plan version of every project, as a subquery."""
    latest = (
        select(PlanVersion.project_id, func.max(PlanVersion.number).label("number"))
        .group_by(PlanVersion.project_id)
        .subquery()
    )
    return select(PlanVersion.id).join(
        latest,
        (PlanVersion.project_id == latest.c.project_id) & (PlanVersion.number == latest.c.number),
    )


def current_allocations(
    session: Session, start: date, end: date, resource_ids: list[int] | None = None
) -> list[PlanAllocation]:
    """Allocations overlapping the period in each project's latest version, plus the bucket
    reservations no later import replaced."""
    current_versions = current_version_ids()
    stmt = select(PlanAllocation).where(
        PlanAllocation.start_on <= end,
        PlanAllocation.end_on >= start,
        (PlanAllocation.plan_version_id.in_(current_versions))
        | (
            PlanAllocation.activity_bucket_id.is_not(None)
            & PlanAllocation.replaced_by_batch_id.is_(None)
        ),
    )
    if resource_ids is not None:
        stmt = stmt.where(PlanAllocation.resource_id.in_(resource_ids))
    return list(session.scalars(stmt.order_by(PlanAllocation.start_on, PlanAllocation.id)))


def spread(session: Session, allocation: PlanAllocation) -> dict[date, Decimal]:
    """An allocation's hours split evenly over its working days (all on its first day if none)."""
    days = working_days(session, allocation.start_on, allocation.end_on) or [allocation.start_on]
    share = allocation.hours / len(days)
    return dict.fromkeys(days, share)


def capacity(
    session: Session, start: date, end: date, resource_ids: list[int] | None = None
) -> list[ResourceLoad]:
    """Available, absent and assigned hours per day for each active resource."""
    stmt = select(Resource).where(Resource.is_active)
    if resource_ids is not None:
        stmt = stmt.where(Resource.id.in_(resource_ids))
    loads = {r.id: ResourceLoad(r) for r in session.scalars(stmt.order_by(Resource.display_name))}
    days = working_days(session, start, end)
    standard = _standard_hours(session, start, end)
    for load in loads.values():
        daily = load.resource.daily_hours
        for day in days:
            load.available[day] = daily if daily is not None else standard[day]

    absences = session.scalars(
        select(Absence).where(
            Absence.resource_id.in_(loads),
            Absence.status == ApprovalStatus.APPROVED.value,
            Absence.start_on <= end,
            Absence.end_on >= start,
        )
    )
    for absence in absences:
        load = loads[absence.resource_id]
        for day in days:
            if absence.start_on <= day <= absence.end_on:
                off = min(absence.hours_per_day or load.available[day], load.available[day])
                load.absent[day] = load.absent.get(day, Decimal(0)) + off
                load.available[day] = max(load.available[day] - off, Decimal(0))

    for allocation in current_allocations(session, start, end, list(loads)):
        load = loads[allocation.resource_id]
        for day, hours in spread(session, allocation).items():
            if start <= day <= end:
                load.assigned[day] += hours
    return list(loads.values())


def weeks(start: date, count: int) -> list[tuple[date, date]]:
    """``count`` Monday-to-Sunday weeks from the week containing ``start``."""
    monday = start - timedelta(days=start.weekday())
    return [
        (monday + timedelta(weeks=n), monday + timedelta(weeks=n, days=6)) for n in range(count)
    ]
