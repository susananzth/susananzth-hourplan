"""Fictitious demo data: builds two workbooks in the usual layout and imports them.

Going through the importer keeps the demo honest (it is the same path real data takes) and
needs no second way of writing the domain. Names, codes and hours are invented and
deterministic.
"""

import random
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path

from openpyxl import Workbook
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from hourplan.catalogs.models import CatalogItem
from hourplan.db import transaction
from hourplan.imports import plan, services
from hourplan.imports.mapping import Mapping
from hourplan.imports.models import ImportBatch
from hourplan.projects import cycles
from hourplan.projects.models import CycleOutcome, Project, ProjectPhase
from hourplan.reports import compare, notes
from hourplan.timesheet.models import TimeEntry

RESOURCES = ("Ana Demo", "Luis Demo", "Marta Demo", "Pablo Demo")
PROJECTS = (
    ("PJ-00001", "Portal de clientes", "Alta"),
    ("PJ-00002", "Facturación electrónica", "Media"),
    ("PJ-00003", "Migración de reportes", "Baja"),
)
PHASES = ("Análisis", "Desarrollo", "Pruebas")
TASK_TYPES = ("Codificación", "Reunión", "Documentación")
APPLICATIONS = ("APP-A", "APP-B")
MONTHS_ES = ("Ene", "Feb", "Mar", "Abr", "May", "Jun", "Jul", "Ago", "Sep", "Oct", "Nov", "Dic")
HOURS_HEADER = (
    "Recurso", "Tipo", "Código", "Título - PRY/TK", "Fase", "Aplicación", "Tipo Tarea",
    "Descripción",
)  # fmt: skip
PLAN_HEADER = (
    "Recurso", "Tipo", "Código", "Título - PRY/TK", "Fase", "Aplicación", "Inicio", "Fin", "Flag",
)  # fmt: skip
BACKLOG_HEADER = (
    "", "Código", "Empresa", "Título", "st-def", "ini-def", "fin-def", "st-des", "ini-des",
    "fin-des", "Status", "Prioridad", "Esfuerzo (horas)",
)  # fmt: skip


class DemoRefusedError(Exception):
    """The database already has hours; the demo never mixes with real data."""


def load_demo(
    session: Session, workdir: Path, mapping: Mapping, today: date | None = None
) -> dict[str, object]:
    """Import the demo hours and plan; refuses when time entries already exist."""
    if session.scalar(select(func.count()).select_from(TimeEntry)):
        raise DemoRefusedError("La base ya tiene horas; los datos demo solo van en una base vacía.")
    today = today or datetime.now(UTC).date()
    hours_file = hours_workbook(workdir / "demo-horas.xlsx", today)
    plan_file = plan_workbook(workdir / "demo-plan.xlsx", today)
    first: ImportBatch = services.import_hours(session, hours_file, mapping=mapping)
    second: ImportBatch = plan.import_plan(session, plan_file, mapping=mapping)
    lifecycle(session, today)
    return {"hours_batch": first.id, "plan_batch": second.id, **first.stats}


def _link_phases(session: Session) -> None:
    """Point imported hours at the project phase with the same name as their source phase."""
    phase_id = (
        select(ProjectPhase.id)
        .where(
            ProjectPhase.project_id == TimeEntry.project_id,
            func.lower(ProjectPhase.name) == func.lower(TimeEntry.source_phase),
        )
        .scalar_subquery()
    )
    with transaction(session):
        session.execute(
            update(TimeEntry)
            .where(TimeEntry.project_phase_id.is_(None), TimeEntry.source_phase.is_not(None))
            .values(project_phase_id=phase_id)
        )


def lifecycle(session: Session, today: date) -> None:
    """Rounds of verification and a closed project, so the reports have something to show.

    PJ-00001 has a returned round and a new one waiting; PJ-00003 went through a returned
    round, was approved and then closed with a deviation note.
    """
    _link_phases(session)
    category = session.scalar(
        select(CatalogItem).where(
            CatalogItem.kind == "rework_category", CatalogItem.code == "EXECUTION"
        )
    )
    if category is None:
        return
    for code, steps in (
        ("PJ-00001", ((-20, -18, False), (-10, None, None))),
        ("PJ-00003", ((-30, -27, False), (-20, -18, True))),
    ):
        project = session.scalar(select(Project).where(Project.code == code))
        phase = session.scalar(
            select(ProjectPhase).where(
                ProjectPhase.project_id == (project.id if project else 0),
                ProjectPhase.name == "Pruebas",
            )
        )
        if project is None or phase is None:
            continue
        for sent, resolved, approved in steps:
            cycle = cycles.submit(session, phase.id, today + timedelta(days=sent), actor_id=None)
            if resolved is None:
                continue
            decision = cycles.Decision(
                outcome=CycleOutcome.APPROVED if approved else CycleOutcome.RETURNED,
                resolved_on=today + timedelta(days=resolved),
                observations=None if approved else "Falta cubrir el caso de fechas límite.",
                rework_category_id=None if approved else category.id,
            )
            cycles.decide(session, cycle.id, decision, decider_id=None)
        if code == "PJ-00003":
            compare.close_project(session, project.id, today - timedelta(days=14), actor_id=None)
            notes.add_note(
                session,
                project.id,
                "Las horas superaron lo planificado por una ronda de pruebas observada.",
                actor_id=None,
            )


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min)


def hours_workbook(path: Path, today: date) -> Path:
    """Eight past weeks of hours, one sheet per resource, 8 h per working day."""
    rng = random.Random(42)  # noqa: S311 - deterministic demo data, not security
    start = _monday(today) - timedelta(weeks=8)
    days = [start + timedelta(days=i) for i in range(56)]
    months = sorted({date(d.year, d.month, 1) for d in days})
    book = Workbook()
    book.remove(book.worksheets[0])
    for number, name in enumerate(RESOURCES):
        sheet = book.create_sheet(name)
        sheet.append([*HOURS_HEADER, *map(_midnight, months), *map(_midnight, days)])
        rows = [
            ("Proyecto", code, title, rng.choice(PHASES), rng.choice(APPLICATIONS))
            for code, title, _ in PROJECTS[: 2 + number % 2]
        ]
        rows += [("Ticket", str(40100 + number), "Error en consulta", "Desarrollo", "APP-A")]
        rows += [("Reuniones", "", "", "", "")]
        cells: dict[int, list[float | None]] = {i: [None] * len(days) for i in range(len(rows))}
        for index, day in enumerate(days):
            if day.weekday() >= 5:
                continue
            left = 8.0
            for row in rng.sample(range(len(rows)), k=min(3, len(rows))):
                share = min(left, rng.choice((1.0, 2.0, 3.0, 4.0)))
                cells[row][index], left = share, left - share
            if left:
                cells[len(rows) - 1][index] = (cells[len(rows) - 1][index] or 0) + left
        for row, (kind, code, title, phase, app) in enumerate(rows):
            task = rng.choice(TASK_TYPES)
            monthly = [
                sum(v or 0 for v, d in zip(cells[row], days, strict=True) if d.month == m.month)
                or None
                for m in months
            ]
            sheet.append([name, kind, code, title, phase, app, task, "", *monthly, *cells[row]])
        if number == 0:  # one vacation day, so absences show up too
            off: list[int | None] = [None] * len(days)
            off[days.index(start + timedelta(days=11))] = 8
            sheet.append(
                [name, "Vacaciones", "", "", "", "", "", "", *([None] * len(months)), *off]
            )
    book.save(path)
    return path


def plan_workbook(path: Path, today: date) -> Path:
    """Backlog sheet and a four-week plan (this week and the next three)."""
    start = _monday(today)
    days = [start + timedelta(days=i) for i in range(28)]
    months = sorted({(d.year, d.month) for d in days})
    book = Workbook()
    book.remove(book.worksheets[0])
    backlog = book.create_sheet()
    backlog.title = "BACKLOG"
    backlog.append([])
    backlog.append(list(BACKLOG_HEADER))
    for index, (code, title, priority) in enumerate(PROJECTS):
        before = start - timedelta(days=30)
        backlog.append(
            [None, code, "Cartera Demo", title, "Completado", _midnight(before),
             _midnight(before + timedelta(days=14)), "En curso",
             _midnight(before + timedelta(days=16)),
             _midnight(start + timedelta(days=30 + 10 * index)),
             "En desarrollo", priority, 80 + 40 * index]
        )  # fmt: skip
    planning = book.create_sheet("Planning")
    planning.append([])
    planning.append([])
    month_names = [f"{MONTHS_ES[m - 1]}-{str(y)[2:]}" for y, m in months]
    day_names = [f"{d.day}-{MONTHS_ES[d.month - 1]}" for d in days]
    planning.append([*PLAN_HEADER, *month_names, *day_names])
    for number, name in enumerate(RESOURCES):
        code, title, _ = PROJECTS[number % len(PROJECTS)]
        for phase, weeks in (("Desarrollo", (0, 1)), ("Pruebas", (2, 3))):
            hours = [
                6 if d.weekday() < 5 and (d - start).days // 7 in weeks else None for d in days
            ]
            monthly = [
                sum(h or 0 for h, d in zip(hours, days, strict=True) if (d.year, d.month) == m)
                or None
                for m in months
            ]
            planning.append(
                [name, "Proyecto", code, title, phase, "APP-A", _midnight(start),
                 _midnight(days[-1]), None, *monthly, *hours]
            )  # fmt: skip
    book.save(path)
    return path


def total_hours(session: Session) -> Decimal:
    return session.scalar(select(func.sum(TimeEntry.hours))) or Decimal(0)
