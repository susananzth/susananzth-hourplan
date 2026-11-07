"""Time entry rules: quarter-hour entries, daily total warnings, edit by a leader, repeat a day.

Nothing here commits except through ``transaction``; authorisation (who may touch whose
entries) is decided by the caller, which tells us whether the editor owns the entry.
"""

from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.db import transaction
from hourplan.policies.models import Holiday, WorkPolicy
from hourplan.policies.services import policy_at
from hourplan.projects.models import Project, ProjectPhase
from hourplan.resources.models import Resource
from hourplan.timesheet.models import Absence, ApprovalStatus, EntrySource, TimeEntry

DEFAULT_STANDARD_HOURS = Decimal(8)
DEFAULT_WARNING_HOURS = Decimal(10)
QUARTER = Decimal("0.25")
ENTITY = "time_entry"

_EDITABLE = (
    "work_date",
    "hours",
    "project_id",
    "activity_bucket_id",
    "project_phase_id",
    "task_type_id",
    "application_id",
    "description",
    "ai_assisted",
)


class EntryError(ValueError):
    pass


class EntryNotFoundError(EntryError):
    pass


class EntryData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    work_date: date
    hours: Decimal = Field(gt=0, le=24)
    project_id: int | None = None
    activity_bucket_id: int | None = None
    project_phase_id: int | None = None
    task_type_id: int | None = None
    application_id: int | None = None
    description: str | None = Field(default=None, max_length=2000)
    ai_assisted: bool = False

    @field_validator("hours")
    @classmethod
    def _quarter_hours(cls, value: Decimal) -> Decimal:
        if value % QUARTER != 0:
            raise ValueError("hours must be a multiple of 0.25")
        return value

    @field_validator("description")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.project_id is None) == (self.activity_bucket_id is None):
            raise ValueError("exactly one of project_id or activity_bucket_id is required")
        if self.project_phase_id is not None and self.project_id is None:
            raise ValueError("a phase requires a project")
        return self


@dataclass(frozen=True)
class DaySummary:
    day: date
    worked: Decimal
    standard: Decimal
    warning_at: Decimal
    absence: Decimal
    holiday: str | None

    @property
    def over_warning(self) -> bool:
        """Past the soft limit: the user is warned, never blocked."""
        return self.worked > self.warning_at

    @property
    def overtime(self) -> Decimal:
        """Hours above the working day (which absences shorten)."""
        return max(self.worked - max(self.standard - self.absence, Decimal(0)), Decimal(0))


def day_summary(session: Session, resource: Resource, day: date) -> DaySummary:
    policy = policy_at(session, WorkPolicy, day)
    standard = resource.daily_hours or (
        policy.standard_daily_hours if policy else DEFAULT_STANDARD_HOURS
    )
    warning_at = policy.daily_warning_hours if policy else DEFAULT_WARNING_HOURS
    worked = session.scalar(
        select(func.coalesce(func.sum(TimeEntry.hours), 0)).where(
            TimeEntry.resource_id == resource.id, TimeEntry.work_date == day
        )
    )
    absence = session.scalar(
        select(func.coalesce(func.sum(Absence.hours_per_day), 0)).where(
            Absence.resource_id == resource.id,
            Absence.status == ApprovalStatus.APPROVED.value,
            Absence.start_on <= day,
            Absence.end_on >= day,
        )
    )
    full_day = session.scalar(
        select(func.count(Absence.id)).where(
            Absence.resource_id == resource.id,
            Absence.status == ApprovalStatus.APPROVED.value,
            Absence.hours_per_day.is_(None),
            Absence.start_on <= day,
            Absence.end_on >= day,
        )
    )
    holiday = session.scalar(select(Holiday.name).where(Holiday.holiday_date == day))
    return DaySummary(
        day=day,
        worked=Decimal(worked or 0),
        standard=standard,
        warning_at=warning_at,
        absence=standard if full_day else Decimal(absence or 0),
        holiday=holiday,
    )


def _resource(session: Session, resource_id: int) -> Resource:
    resource = session.get(Resource, resource_id)
    if resource is None:
        raise EntryError("Unknown resource")
    return resource


def _check_phase(session: Session, data: EntryData) -> None:
    if data.project_phase_id is None:
        return
    phase = session.get(ProjectPhase, data.project_phase_id)
    if phase is None or phase.project_id != data.project_id:
        raise EntryError("The phase does not belong to the project")


def create_entry(
    session: Session,
    resource_id: int,
    data: EntryData,
    *,
    actor_id: int | None,
    source: EntrySource = EntrySource.MANUAL,
) -> tuple[TimeEntry, DaySummary]:
    """Store the entry; the summary says whether the day now passes the warning limit."""
    resource = _resource(session, resource_id)
    _check_phase(session, data)
    with transaction(session):
        entry = TimeEntry(
            resource_id=resource_id,
            source=source.value,
            created_by_id=actor_id,
            **data.model_dump(),
        )
        session.add(entry)
        session.flush()
        summary = day_summary(session, resource, data.work_date)
    return entry, summary


def _get_entry(session: Session, entry_id: int) -> TimeEntry:
    entry = session.get(TimeEntry, entry_id)
    if entry is None:
        raise EntryNotFoundError("Unknown time entry")
    return entry


def _subject_user_id(session: Session, entry: TimeEntry) -> int | None:
    resource = session.get(Resource, entry.resource_id)
    return resource.user_id if resource else None


def _snapshot(entry: TimeEntry) -> dict[str, Any]:
    return {key: getattr(entry, key) for key in _EDITABLE}


def update_entry(
    session: Session,
    entry_id: int,
    data: EntryData,
    *,
    actor_id: int | None,
    is_owner: bool,
    reason: str | None = None,
) -> tuple[TimeEntry, DaySummary]:
    """Edit an entry. Someone other than the owner (a leader) must give a justification."""
    entry = _get_entry(session, entry_id)
    resource = _resource(session, entry.resource_id)
    _check_phase(session, data)
    new = data.model_dump()
    changes = audit.diff(_snapshot(entry), new)
    with transaction(session):
        if changes:
            audit.record(
                session,
                action=AuditAction.UPDATE,
                entity_type=ENTITY,
                entity_id=entry.id,
                actor_id=actor_id,
                changes=changes,
                reason=reason,
                reason_required=not is_owner,
                subject_user_id=_subject_user_id(session, entry),
            )
            for key, value in new.items():
                setattr(entry, key, value)
            entry.updated_at = func.now()
        session.flush()
        summary = day_summary(session, resource, data.work_date)
    return entry, summary


def delete_entry(
    session: Session,
    entry_id: int,
    *,
    actor_id: int | None,
    is_owner: bool,
    reason: str | None = None,
) -> None:
    entry = _get_entry(session, entry_id)
    with transaction(session):
        audit.record(
            session,
            action=AuditAction.DELETE,
            entity_type=ENTITY,
            entity_id=entry.id,
            actor_id=actor_id,
            changes={key: [value, None] for key, value in _snapshot(entry).items()},
            reason=reason,
            reason_required=not is_owner,
            subject_user_id=_subject_user_id(session, entry),
        )
        session.delete(entry)


def previous_entry_day(session: Session, resource_id: int, before: date) -> date | None:
    """The last day before ``before`` on which the resource logged hours ("yesterday")."""
    return session.scalar(
        select(func.max(TimeEntry.work_date)).where(
            TimeEntry.resource_id == resource_id, TimeEntry.work_date < before
        )
    )


def repeat_day(
    session: Session,
    resource_id: int,
    target_day: date,
    *,
    actor_id: int | None,
    source_day: date | None = None,
) -> list[TimeEntry]:
    """Copy the entries of ``source_day`` (default: the last day with entries) to ``target_day``.

    Refuses to run on a day that already has hours, so pressing the button twice cannot
    double the day.
    """
    resource = _resource(session, resource_id)
    source_day = source_day or previous_entry_day(session, resource_id, target_day)
    if source_day is None or source_day == target_day:
        raise EntryError("There is no earlier day to repeat")
    if day_summary(session, resource, target_day).worked:
        raise EntryError("The day already has hours")
    originals = session.scalars(
        select(TimeEntry)
        .where(TimeEntry.resource_id == resource_id, TimeEntry.work_date == source_day)
        .order_by(TimeEntry.id)
    ).all()
    if not originals:
        raise EntryError("The source day has no entries")
    with transaction(session):
        copies = [
            TimeEntry(
                resource_id=resource_id,
                work_date=target_day,
                source=EntrySource.MANUAL.value,
                created_by_id=actor_id,
                **{k: getattr(o, k) for k in _EDITABLE if k != "work_date"},
            )
            for o in originals
        ]
        session.add_all(copies)
        session.flush()
    return copies


def working_days(session: Session, start: date, end: date) -> list[date]:
    """Monday to Friday between the dates, minus configured holidays."""
    holidays = set(
        session.scalars(
            select(Holiday.holiday_date).where(Holiday.holiday_date.between(start, end))
        )
    )
    days = (start + timedelta(days=n) for n in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5 and d not in holidays]


@dataclass(frozen=True)
class RowKey:
    """A grid row: a project or an activity bucket, optionally with a phase and task type."""

    project_id: int | None = None
    activity_bucket_id: int | None = None
    project_phase_id: int | None = None
    task_type_id: int | None = None


@dataclass(frozen=True)
class Cell:
    day: date
    hours: Decimal
    entry_id: int | None
    # Several entries on one cell cannot be edited as a single number.
    multiple: bool = False


@dataclass(frozen=True)
class GridRow:
    key: RowKey
    label: str
    cells: list[Cell]

    @property
    def total(self) -> Decimal:
        return sum((c.hours for c in self.cells), Decimal(0))


@dataclass(frozen=True)
class WeekGrid:
    days: list[date]
    rows: list[GridRow]
    summaries: list[DaySummary]

    @property
    def total(self) -> Decimal:
        return sum((s.worked for s in self.summaries), Decimal(0))


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _row_label(
    project: Project | None, bucket: CatalogItem | None, phase: ProjectPhase | None
) -> str:
    label = f"{project.code} · {project.title}" if project else (bucket.name if bucket else "?")
    return f"{label} ({phase.name})" if phase else label


def week_grid(
    session: Session, resource: Resource, start: date, *, extra_rows: list[RowKey] | None = None
) -> WeekGrid:
    """Seven days of the resource, one row per distinct target; ``extra_rows`` are shown empty."""
    start = week_start(start)
    days = [start + timedelta(days=n) for n in range(7)]
    entries = session.scalars(
        select(TimeEntry)
        .where(TimeEntry.resource_id == resource.id, TimeEntry.work_date.between(days[0], days[-1]))
        .order_by(TimeEntry.id)
    ).all()
    grouped: dict[RowKey, dict[date, list[TimeEntry]]] = {}
    for item in entries:
        key = RowKey(
            item.project_id, item.activity_bucket_id, item.project_phase_id, item.task_type_id
        )
        grouped.setdefault(key, {}).setdefault(item.work_date, []).append(item)
    for key in extra_rows or []:
        grouped.setdefault(key, {})

    projects = {
        p.id: p
        for p in session.scalars(
            select(Project).where(Project.id.in_({k.project_id for k in grouped if k.project_id}))
        )
    }
    buckets = {
        b.id: b
        for b in session.scalars(
            select(CatalogItem).where(
                CatalogItem.id.in_({k.activity_bucket_id for k in grouped if k.activity_bucket_id})
            )
        )
    }
    phases = {
        p.id: p
        for p in session.scalars(
            select(ProjectPhase).where(
                ProjectPhase.id.in_({k.project_phase_id for k in grouped if k.project_phase_id})
            )
        )
    }
    rows = []
    for key, by_day in grouped.items():
        cells = []
        for day in days:
            found = by_day.get(day, [])
            cells.append(
                Cell(
                    day=day,
                    hours=sum((e.hours for e in found), Decimal(0)),
                    entry_id=found[0].id if len(found) == 1 else None,
                    multiple=len(found) > 1,
                )
            )
        label = _row_label(
            projects.get(key.project_id or 0),
            buckets.get(key.activity_bucket_id or 0),
            phases.get(key.project_phase_id or 0),
        )
        rows.append(GridRow(key=key, label=label, cells=cells))
    rows.sort(key=lambda r: r.label.lower())
    return WeekGrid(days, rows, [day_summary(session, resource, d) for d in days])


def set_cell(
    session: Session,
    resource_id: int,
    key: RowKey,
    day: date,
    hours: Decimal,
    *,
    actor_id: int | None,
) -> DaySummary:
    """Set the hours of one grid cell: create, change or (with 0) delete its single entry."""
    resource = _resource(session, resource_id)
    found = session.scalars(
        select(TimeEntry).where(
            TimeEntry.resource_id == resource_id,
            TimeEntry.work_date == day,
            TimeEntry.project_id.is_not_distinct_from(key.project_id),
            TimeEntry.activity_bucket_id.is_not_distinct_from(key.activity_bucket_id),
            TimeEntry.project_phase_id.is_not_distinct_from(key.project_phase_id),
            TimeEntry.task_type_id.is_not_distinct_from(key.task_type_id),
        )
    ).all()
    if len(found) > 1:
        raise EntryError("The cell has several entries and cannot be edited as one")
    if not found:
        if hours == 0:
            return day_summary(session, resource, day)
        data = EntryData(work_date=day, hours=hours, **asdict(key))
        return create_entry(session, resource_id, data, actor_id=actor_id)[1]
    current = found[0]
    if hours == 0:
        delete_entry(session, current.id, actor_id=actor_id, is_owner=True)
        return day_summary(session, resource, day)
    data = EntryData(
        **{**{k: getattr(current, k) for k in _EDITABLE}, "hours": hours},
    )
    return update_entry(session, current.id, data, actor_id=actor_id, is_owner=True)[1]


@dataclass(frozen=True)
class Target:
    key: RowKey
    label: str


def search_targets(session: Session, text: str, *, limit: int = 10) -> list[Target]:
    """Open projects or tickets by code or title, and active activity buckets by name."""
    text = text.strip()
    if not text:
        return []
    pattern = f"%{text.replace('%', r'\%').replace('_', r'\_')}%"
    projects = session.scalars(
        select(Project)
        .where(
            Project.closed_on.is_(None),
            Project.code.ilike(pattern) | Project.title.ilike(pattern),
        )
        .order_by(Project.code)
        .limit(limit)
    )
    buckets = session.scalars(
        select(CatalogItem)
        .where(
            CatalogItem.kind == CatalogKind.ACTIVITY_BUCKET.value,
            CatalogItem.is_active.is_(True),
            CatalogItem.name.ilike(pattern),
        )
        .order_by(CatalogItem.name)
        .limit(limit)
    )
    found = [Target(RowKey(project_id=p.id), _row_label(p, None, None)) for p in projects]
    found += [Target(RowKey(activity_bucket_id=b.id), b.name) for b in buckets]
    return found[:limit]
