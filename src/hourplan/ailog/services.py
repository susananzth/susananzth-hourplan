"""Turn a parsed log or calendar into an editable draft and save the confirmed lines."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.ailog.parsers import LogRow, Meeting
from hourplan.catalogs.models import Application, CatalogItem, CatalogKind
from hourplan.projects.models import Project
from hourplan.shared.labels import validation_message
from hourplan.timesheet import services as timesheet
from hourplan.timesheet.models import EntrySource, TimeEntry
from hourplan.timesheet.services import EntryData

MAX_LINES = 200


class AilogError(Exception):
    """A message for the person, in Spanish."""


@dataclass(frozen=True)
class Option:
    id: int
    label: str
    code: str | None = None


@dataclass(frozen=True)
class Choices:
    projects: tuple[Option, ...]
    buckets: tuple[Option, ...]
    task_types: tuple[Option, ...]
    applications: tuple[Option, ...]


def choices(session: Session) -> Choices:
    projects = session.scalars(
        select(Project).where(Project.closed_on.is_(None)).order_by(Project.code)
    ).all()

    def items(kind: CatalogKind) -> tuple[Option, ...]:
        rows = session.scalars(
            select(CatalogItem)
            .where(CatalogItem.kind == kind.value, CatalogItem.is_active.is_(True))
            .order_by(CatalogItem.name)
        )
        return tuple(Option(r.id, r.name) for r in rows)

    apps = session.scalars(
        select(Application).where(Application.is_active.is_(True)).order_by(Application.name)
    )
    return Choices(
        projects=tuple(Option(p.id, p.title, p.code) for p in projects),
        buckets=items(CatalogKind.ACTIVITY_BUCKET),
        task_types=items(CatalogKind.TASK_TYPE),
        applications=tuple(Option(a.id, a.name, a.code) for a in apps),
    )


def _already_saved(
    session: Session, resource_id: int, work_date: date, hours: Decimal, text: str | None
) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(TimeEntry)
            .where(
                TimeEntry.resource_id == resource_id,
                TimeEntry.work_date == work_date,
                TimeEntry.hours == hours,
                TimeEntry.description == text,
                TimeEntry.source.in_([EntrySource.AI_LOG.value, EntrySource.IMPORT.value]),
            )
        )
        or 0
    ) > 0


def _line(
    work_date: date, hours: Decimal, description: str | None, ai_assisted: bool
) -> dict[str, Any]:
    return {
        "use": True,
        "work_date": work_date.isoformat(),
        "hours": str(hours),
        "project_id": None,
        "activity_bucket_id": None,
        "project_phase_id": None,
        "task_type_id": None,
        "application_id": None,
        "description": description,
        "ai_assisted": ai_assisted,
        "notes": [],
    }


def draft_from_log(session: Session, resource_id: int, rows: list[LogRow]) -> list[dict[str, Any]]:
    """Resolve each code against open projects and tickets; unknown codes stay for the person."""
    by_code: dict[str, list[Project]] = {}
    for project in session.scalars(select(Project).where(Project.closed_on.is_(None))):
        by_code.setdefault(project.code.lower(), []).append(project)
    lines: list[dict[str, Any]] = []
    for row in rows:
        line = _line(row.work_date, row.hours, row.description, True)
        found = by_code.get(row.code.lower(), [])
        if len(found) == 1:
            line["project_id"] = found[0].id
        elif found:
            line["project_id"] = found[0].id
            line["notes"].append(
                f"El código {row.code} existe como proyecto y como ticket; revisa."
            )
        else:
            line["use"] = False
            line["notes"].append(f"No se encontró el código «{row.code}»: elige el proyecto.")
        if _already_saved(session, resource_id, row.work_date, row.hours, row.description):
            line["use"] = False
            line["notes"].append("Parece ya guardada antes; márcala si quieres repetirla.")
        lines.append(line)
    return lines


def draft_from_calendar(
    session: Session, resource_id: int, meetings: list[Meeting]
) -> list[dict[str, Any]]:
    """Meetings go to the meeting bucket when there is one; the person picks the project."""
    bucket = next((b for b in choices(session).buckets if b.label.lower().startswith("reun")), None)
    lines: list[dict[str, Any]] = []
    for meeting in meetings:
        line = _line(meeting.work_date, meeting.hours, meeting.title, False)
        if bucket is not None:
            line["activity_bucket_id"] = bucket.id
        else:
            line["notes"].append("No hay actividad «Reunión»: elige a dónde van estas horas.")
        if _already_saved(session, resource_id, meeting.work_date, meeting.hours, meeting.title):
            line["use"] = False
            line["notes"].append("Parece ya guardada antes; márcala si quieres repetirla.")
        lines.append(line)
    return lines


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", ".").strip())
    except InvalidOperation as err:
        raise AilogError("Las horas no son un número.") from err


def build_entries(rows: list[dict[str, Any]]) -> list[EntryData]:
    """Validate every chosen line before anything is saved; the message says which one failed."""
    entries: list[EntryData] = []
    for number, row in enumerate(rows, start=1):
        try:
            entries.append(
                EntryData(
                    work_date=date.fromisoformat(str(row.get("work_date"))),
                    hours=_decimal(str(row.get("hours", ""))),
                    project_id=row.get("project_id"),
                    activity_bucket_id=row.get("activity_bucket_id"),
                    task_type_id=row.get("task_type_id"),
                    application_id=row.get("application_id"),
                    description=row.get("description"),
                    ai_assisted=bool(row.get("ai_assisted")),
                )
            )
        except (ValidationError, ValueError, AilogError) as err:
            message = validation_message(err) if isinstance(err, ValidationError) else str(err)
            raise AilogError(f"Línea {number}: {message}") from err
    if not entries:
        raise AilogError("Marca al menos una línea para guardar.")
    return entries


def confirm(
    session: Session,
    resource_id: int,
    rows: list[dict[str, Any]],
    *,
    source: EntrySource,
    actor_id: int | None,
) -> int:
    entries = build_entries(rows)
    for number, data in enumerate(entries, start=1):
        try:
            timesheet.create_entry(session, resource_id, data, actor_id=actor_id, source=source)
        except timesheet.EntryError as err:
            session.rollback()
            raise AilogError(f"Línea {number}: {err}") from err
    return len(entries)
