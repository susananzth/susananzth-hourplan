"""Exports the recorded hours in the layout of the Excel hours sheets (one sheet per resource).

The file can be read back by ``import-hours``: same columns, a monthly-totals block and then
one column per day.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.catalogs.models import Application, CatalogItem
from hourplan.imports.mapping import Mapping
from hourplan.projects.models import PhaseTemplateStep, Project, ProjectKind, ProjectPhase
from hourplan.resources.models import Resource
from hourplan.timesheet.models import Absence, ApprovalStatus, TimeEntry

HEADERS = (
    "Recurso", "Tipo", "Código", "Título - PRY/TK", "Fase", "Aplicación", "Tipo Tarea",
    "Descripción",
)  # fmt: skip
INVALID_SHEET_CHARS = str.maketrans("", "", "[]:*?/\\")
DEFAULT_WORKDAY = Decimal(8)

# (type, code, title, phase, application, task type, description)
RowKey = tuple[str, str, str, str, str, str, str]


def export_hours(
    session: Session,
    path: Path,
    mapping: Mapping,
    *,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, str]:
    """Write the workbook; returns totals (``sheets``, ``rows``, ``hours``, ``absence_hours``)."""
    entries = _entries(session, mapping, start, end)
    absences = _absences(session, mapping, start, end)
    days = [
        d
        for per_key in (*entries.values(), *absences.values())
        for rows in per_key.values()
        for d in rows
    ]
    if not days:
        raise ValueError("No hay horas en ese período.")
    first, last = start or min(days), end or max(days)
    # The importer only reads sheets with enough day columns; pad short periods with empty days.
    last = max(last, first + timedelta(days=mapping.min_day_columns - 1))

    book = Workbook()
    book.remove(book.worksheets[0])
    names = {r.id: r.display_name for r in session.scalars(select(Resource))}
    sheets = rows_count = 0
    hours = absence_hours = Decimal(0)
    used: set[str] = set()
    for resource_id in sorted(set(entries) | set(absences), key=lambda i: names[i].casefold()):
        rows = {**entries.get(resource_id, {}), **absences.get(resource_id, {})}
        sheet = book.create_sheet(_sheet_title(names[resource_id], used))
        _write_sheet(sheet, names[resource_id], rows, first, last)
        sheets += 1
        rows_count += len(rows)
        hours += sum(
            (h for r in entries.get(resource_id, {}).values() for h in r.values()), Decimal(0)
        )
        absence_hours += sum(
            (h for r in absences.get(resource_id, {}).values() for h in r.values()), Decimal(0)
        )
    book.save(path)
    return {
        "sheets": str(sheets), "rows": str(rows_count), "hours": str(hours),
        "absence_hours": str(absence_hours),
    }  # fmt: skip


def _entries(
    session: Session, mapping: Mapping, start: date | None, end: date | None
) -> dict[int, dict[RowKey, dict[date, Decimal]]]:
    query = (
        select(TimeEntry, Project, CatalogItem, Application, PhaseTemplateStep.name)
        .outerjoin(Project, Project.id == TimeEntry.project_id)
        .outerjoin(CatalogItem, CatalogItem.id == TimeEntry.activity_bucket_id)
        .outerjoin(Application, Application.id == TimeEntry.application_id)
        .outerjoin(ProjectPhase, ProjectPhase.id == TimeEntry.project_phase_id)
        .outerjoin(PhaseTemplateStep, PhaseTemplateStep.id == ProjectPhase.template_step_id)
        .order_by(TimeEntry.work_date, TimeEntry.id)
    )
    if start:
        query = query.where(TimeEntry.work_date >= start)
    if end:
        query = query.where(TimeEntry.work_date <= end)
    task_types = {c.id: c.name for c in session.scalars(select(CatalogItem))}
    labels = _kind_labels(mapping)
    result: dict[int, dict[RowKey, dict[date, Decimal]]] = defaultdict(lambda: defaultdict(dict))
    for entry, project, bucket, application, step_name in session.execute(query):
        if project is not None:
            kind = labels["ticket" if project.kind == ProjectKind.TICKET else "project"]
            type_text, code, title = kind, project.code, project.title
        else:
            type_text, code, title = (bucket.name if bucket else ""), "", ""
        key = (
            type_text, code, title, entry.source_phase or step_name or "",
            application.name if application else "",
            task_types.get(entry.task_type_id, "") if entry.task_type_id else "",
            entry.description or "",
        )  # fmt: skip
        cells = result[entry.resource_id][key]
        cells[entry.work_date] = cells.get(entry.work_date, Decimal(0)) + entry.hours
    return result


def _absences(
    session: Session, mapping: Mapping, start: date | None, end: date | None
) -> dict[int, dict[RowKey, dict[date, Decimal]]]:
    labels = {value: key.title() for key, value in reversed(mapping.absences.items())}
    default = _kind_labels(mapping)["absence"]
    workdays = {r.id: r.daily_hours or DEFAULT_WORKDAY for r in session.scalars(select(Resource))}
    result: dict[int, dict[RowKey, dict[date, Decimal]]] = defaultdict(lambda: defaultdict(dict))
    query = select(Absence).where(Absence.status == ApprovalStatus.APPROVED.value)
    for absence in session.scalars(query):
        key: RowKey = (labels.get(absence.absence_type, default), "", "", "", "", "", "")
        hours = absence.hours_per_day or workdays[absence.resource_id]
        day = absence.start_on
        while day <= absence.end_on:
            if day.weekday() < 5 and (not start or day >= start) and (not end or day <= end):
                result[absence.resource_id][key][day] = hours
            day += timedelta(days=1)
    return result


def _kind_labels(mapping: Mapping) -> dict[str, str]:
    """Text of the "Tipo" column for each kind, taken from the mapping (first spelling)."""
    labels = {"project": "Proyecto", "ticket": "Ticket", "absence": "Ausencia"}
    for text, kind in reversed(mapping.kinds.items()):
        if kind in labels and kind != "absence":
            labels[kind] = text.title()
    return labels


def _sheet_title(name: str, used: set[str]) -> str:
    base = (name.translate(INVALID_SHEET_CHARS).strip() or "Recurso")[:28]
    title, number = base, 1
    while title.casefold() in used:
        number += 1
        title = f"{base} {number}"
    used.add(title.casefold())
    return title


def _write_sheet(
    sheet: Worksheet,
    name: str,
    rows: dict[RowKey, dict[date, Decimal]],
    first: date,
    last: date,
) -> None:
    months = _months(first, last)
    days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
    sheet.append(
        [*HEADERS, *(_midnight(date(y, m, 1)) for y, m in months), *(_midnight(d) for d in days)]
    )
    for cell in sheet[1][len(HEADERS) : len(HEADERS) + len(months)]:
        cell.number_format = "mmm-yy"
    for cell in sheet[1][len(HEADERS) + len(months) :]:
        cell.number_format = "d-mmm"
    for key in sorted(rows, key=lambda k: (k[0].casefold(), k[1], k[3], k[6])):
        per_day = rows[key]
        monthly = [
            sum((h for d, h in per_day.items() if (d.year, d.month) == month), Decimal(0))
            for month in months
        ]
        sheet.append(
            [
                name, *key,
                *(float(m) if m else None for m in monthly),
                *(float(per_day[d]) if d in per_day else None for d in days),
            ]
        )  # fmt: skip
    sheet.freeze_panes = f"{get_column_letter(len(HEADERS) + 1)}2"


def _months(first: date, last: date) -> list[tuple[int, int]]:
    months, year, month = [], first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min)
