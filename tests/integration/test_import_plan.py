from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from flask import Flask
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.catalogs.models import CatalogItem, CatalogKind, Portfolio
from hourplan.imports import plan, services
from hourplan.imports.models import ImportIssue
from hourplan.planning.models import PlanAllocation, PlanVersion
from hourplan.projects.models import Project, ProjectPhase
from hourplan.resources.models import Resource
from hourplan.setup import services as setup

BACKLOG_GROUPS = [None, None, None, None, "DEF", None, None, "PROPUESTA"]
BACKLOG_HEADER = [
    None, "Código", "Empresa", "Título", "st-def", "ini-def", "fin-def", "st-ps", "ini-ps",
    "fin-ps", "st-des", "ini-des", "fin-des", "st-qa", "ini-qa", "fin-qa", "st-uat", "ini-uat",
    "fin-uat", "f-pase", "stat-pase", "Status", "Prioridad", "ESF-PS", "ESF-DIS", "ESF-DES",
    "ESF-QA", "ESF-PRD", "Esfuerzo (horas)", "Presupuesto", "Bitácora",
]  # fmt: skip
PLAN_TEXT = ["Recurso", "Tipo", "Código", "Título -  PRY/TK", "Fase", "Aplicación", "Inicio",
             "Fin", "flag"]  # fmt: skip
# Monthly block as Excel leaves it ("Feb-26" turned into 26 Feb), then the days: Mon 5 to
# Fri 16 January; "6-Ene" is kept as text, the others as Excel's dates of another year.
MONTHLY = ["Ene-26", datetime(2026, 2, 26), "Mar-26"]
DAYS = [date(2026, 1, d) for d in (5, 6, 7, 8, 9, 12, 13, 14, 15, 16)]
DAY_HEADERS = [
    f"{d.day}-Ene" if i % 2 == 0 else datetime(2020, 1, d.day) for i, d in enumerate(DAYS)
]


def backlog_row(code: str, title: str, **cells: object) -> list[object]:
    row: list[object] = [None] * len(BACKLOG_HEADER)
    row[1], row[2], row[3] = code, "Cartera Uno", title
    for header, value in cells.items():
        row[BACKLOG_HEADER.index(header.replace("_", "-"))] = value
    return row


def plan_row(resource: str, kind: str, code: str, title: str, phase: str,
             hours: dict[int, object]) -> list[object]:  # fmt: skip
    cells: list[object] = [resource, kind, code, title, phase, "ECOM", datetime(2026, 7, 20),
                           datetime(2026, 8, 5), None, 0, 0, 0]  # fmt: skip
    cells += [hours.get(i) for i in range(len(DAYS))]
    return cells


def make_book(path: Path, *, ticket_hours: float = 4, extra_backlog: bool = True) -> Path:
    book = Workbook()
    backlog = book.active
    backlog.title = "BACKLOG"
    backlog.append(BACKLOG_GROUPS)
    backlog.append(BACKLOG_HEADER)
    backlog.append(
        backlog_row(
            "PROY-0001-2026", "Portal de clientes", st_def="Completado",
            ini_def=datetime(2026, 1, 5), fin_def=datetime(2026, 1, 9), st_des="En curso",
            ini_des=datetime(2026, 1, 20), fin_des=datetime(2026, 1, 10), stat_pase="Pendiente",
            f_pase=datetime(2026, 2, 27), Status="En desarrollo", Prioridad="Crítica",
            ESF_PS=4, ESF_DIS=6, ESF_DES=30, **{"Esfuerzo (horas)": 40, "Bitácora": "12/01 ok"},
        )
    )  # fmt: skip
    if extra_backlog:
        backlog.append(backlog_row("PROY-0002-2026-F1", "Fase uno", Status="Backlog"))
    backlog.append(backlog_row("TK-MEJORA", "Sin código"))
    backlog.append(backlog_row("PROY-0001-2026", "Repetido"))
    backlog.append(backlog_row("PROY-0003-2026", "Fecha rota", f_pase="28-.11", st_qa=5))

    planning = book.create_sheet("Planning")
    planning.append([])
    planning.append([None] * 9 + ["ENERO"])
    planning.append([*PLAN_TEXT, *MONTHLY, *DAY_HEADERS])
    week = dict.fromkeys(range(5), 8)
    planning.append(plan_row("Ana Pérez Soto", "Proyecto", "PROY-0001-2026", "Portal", "Desarrollo",
                             {**week, 5: 4, 6: 4}))  # fmt: skip
    planning.append(plan_row("Ana Pérez Soto", "Proyecto", "PROY-0001-2026", "Portal", "Lev.Obs",
                             {7: 2}))  # fmt: skip
    planning.append(plan_row("Ana Pérez Soto", "Ticket", "\xa0", "Atención de tickets",
                             "Desarrollo", {0: ticket_hours, 1: ticket_hours}))  # fmt: skip
    planning.append(plan_row("Ana Pérez Soto", "Onboarding", "", "Inducción", "Otros", {9: 1}))
    planning.append(plan_row("", "Proyecto", "PROY-0001-2026", "Portal", "Análisis", {8: 3}))
    planning.append(plan_row("Ana Pérez Soto", "Proyecto", "PROY-9", "Raro", "Análisis",
                             {8: "x"}))  # fmt: skip
    book.save(path)
    return path


@pytest.fixture
def seeded(session: Session) -> Session:
    setup.apply_seed(session, setup.load_seed(setup.DEFAULT_SEED))
    return session


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    return make_book(tmp_path / "plan.xlsx")


def count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def issue_codes(session: Session, batch_id: int) -> set[str]:
    return set(session.scalars(select(ImportIssue.code).where(ImportIssue.batch_id == batch_id)))


def project(session: Session, code: str) -> Project:
    return session.scalars(select(Project).where(Project.code == code)).one()


def phases(session: Session, code: str) -> dict[str, ProjectPhase]:
    found = session.scalars(
        select(ProjectPhase).where(ProjectPhase.project_id == project(session, code).id)
    )
    return {phase.name: phase for phase in found}


def active_reservations(session: Session) -> list[PlanAllocation]:
    return list(
        session.scalars(
            select(PlanAllocation)
            .where(
                PlanAllocation.plan_version_id.is_(None),
                PlanAllocation.replaced_by_batch_id.is_(None),
            )
            .order_by(PlanAllocation.start_on)
        )
    )


def test_backlog_overwrites_projects_and_fills_phases(seeded: Session, workbook: Path) -> None:
    session = seeded
    existing = Project(code="PROY-0001-2026", title="Viejo")
    session.add(existing)
    session.flush()

    batch = plan.import_plan(session, workbook)

    assert existing.title == "Portal de clientes"
    assert existing.notes == "12/01 ok"
    assert existing.base_effort_hours == Decimal(40)
    portfolio = session.get(Portfolio, existing.portfolio_id)
    assert portfolio is not None
    assert portfolio.name == "Cartera Uno"
    status = session.get(CatalogItem, existing.status_id)
    assert status is not None
    assert status.code == "IN_PROGRESS"
    priority = session.get(CatalogItem, existing.priority_id)
    assert priority is not None
    assert priority.name == "Crítica"
    assert priority.kind == CatalogKind.PRIORITY
    steps = phases(session, "PROY-0001-2026")
    assert len(steps) == 7
    assert steps["Análisis"].status == "done"
    assert (steps["Análisis"].planned_start, steps["Análisis"].planned_end) == (
        date(2026, 1, 5),
        date(2026, 1, 9),
    )
    assert steps["Diseño"].base_effort_hours == Decimal(10)  # ESF-PS + ESF-DIS
    assert steps["Desarrollo"].status == "in_progress"
    assert (steps["Desarrollo"].planned_start, steps["Desarrollo"].planned_end) == (
        date(2026, 1, 20),
        None,
    )
    assert steps["Despliegue"].planned_end == date(2026, 2, 27)
    sub = project(session, "PROY-0002-2026-F1")
    assert sub.parent_id == project(session, "PROY-0002-2026").id
    assert {
        "invalid_project_code", "duplicate_code", "dates_reversed", "bad_date", "bad_phase_status",
    } <= issue_codes(session, batch.id)  # fmt: skip
    assert batch.stats["projects_updated"] == 1
    assert batch.previous["project"][str(existing.id)]["title"] == ["Viejo", "Portal de clientes"]


def test_planning_stores_runs_versions_and_reservations(seeded: Session, workbook: Path) -> None:
    session = seeded
    session.add(Resource(display_name="Ana Pérez"))
    session.flush()

    batch = plan.import_plan(session, workbook)

    # The planning sheet's full name is matched to the existing short one.
    assert count(session, Resource) == 1
    version = session.scalars(select(PlanVersion)).one()
    assert (version.number, version.is_baseline) == (1, True)
    rows = session.scalars(
        select(PlanAllocation)
        .where(PlanAllocation.plan_version_id == version.id)
        .order_by(PlanAllocation.start_on)
    ).all()
    development = phases(session, "PROY-0001-2026")["Desarrollo"].id
    assert [(r.start_on, r.end_on, r.hours, r.project_phase_id) for r in rows] == [
        (date(2026, 1, 5), date(2026, 1, 9), Decimal(40), development),
        (date(2026, 1, 12), date(2026, 1, 13), Decimal(8), development),
        (date(2026, 1, 14), date(2026, 1, 14), Decimal(2), development),  # Lev.Obs
    ]
    reservations = active_reservations(session)
    buckets = [session.get(CatalogItem, r.activity_bucket_id) for r in reservations]
    assert [b.name for b in buckets if b] == [
        "Atención de tickets",
        "Inducción",
    ]  # seeded ONBOARDING
    assert [r.hours for r in reservations] == [Decimal(8), Decimal(1)]
    assert {r.project_phase_id for r in reservations} == {None}
    assert {"resource_matched", "overallocated", "missing_resource", "bad_hours",
            "invalid_project_code"} <= issue_codes(session, batch.id)  # fmt: skip
    assert batch.stats["hours_planned"] == "59"
    assert batch.stats["hours_planned_read"] == "62"


def test_reimport_versions_only_changed_plans(
    seeded: Session, workbook: Path, tmp_path: Path
) -> None:
    session = seeded
    first = plan.import_plan(session, workbook)
    with pytest.raises(services.ImportRejectedError, match="ya se importó"):
        plan.import_plan(session, workbook)

    unchanged = plan.import_plan(session, make_book(tmp_path / "b.xlsx", extra_backlog=False))
    assert unchanged.stats["plan_versions_unchanged"] == 1
    assert unchanged.stats["reservations_unchanged"] == 2
    assert "reservations" not in unchanged.stats

    changed = plan.import_plan(session, make_book(tmp_path / "c.xlsx", ticket_hours=2))
    assert changed.stats["reservations_replaced"] == 1
    assert [r.hours for r in active_reservations(session)] == [Decimal(4), Decimal(1)]
    assert count(session, PlanVersion) == 1
    assert first.id != changed.id


def test_revert_restores_previous_values_and_reservations(
    seeded: Session, workbook: Path, tmp_path: Path
) -> None:
    session = seeded
    existing = Project(code="PROY-0001-2026", title="Viejo")
    session.add(existing)
    session.flush()
    before = {m: count(session, m) for m in (Project, ProjectPhase, Resource, Portfolio)}
    first = plan.import_plan(session, workbook)
    second = plan.import_plan(session, make_book(tmp_path / "c.xlsx", ticket_hours=2))
    existing.title = "Cambiado a mano"
    session.flush()

    services.revert_batch(session, second)
    assert [r.hours for r in active_reservations(session)] == [Decimal(8), Decimal(1)]

    services.revert_batch(session, first)
    assert existing.title == "Cambiado a mano"  # edited after the import: kept
    assert existing.status_id is None
    assert existing.portfolio_id is None
    assert count(session, PlanAllocation) == 0
    assert count(session, PlanVersion) == 0
    assert {m: count(session, m) for m in before} == before


def test_the_same_file_can_feed_both_importers(seeded: Session, workbook: Path) -> None:
    plan.import_plan(seeded, workbook)

    with pytest.raises(services.ImportRejectedError, match="hojas de horas"):
        services.import_hours(seeded, workbook)  # rejected for its content, not as a repeat


def test_without_templates_projects_get_no_phases(session: Session, workbook: Path) -> None:
    batch = plan.import_plan(session, workbook)

    assert count(session, ProjectPhase) == 0
    assert batch.stats["allocations"] == 3
    assert "unknown_phase" in issue_codes(session, batch.id)


def test_workbook_without_plan_sheets_is_rejected(session: Session, tmp_path: Path) -> None:
    book = Workbook()
    book.active.append(["Total"])
    path = tmp_path / "vacio.xlsx"
    book.save(path)

    with pytest.raises(services.ImportRejectedError, match="backlog ni de planificación"):
        plan.import_plan(session, path)


def test_cli_import_plan(app: Flask, workbook: Path) -> None:
    runner = app.test_cli_runner()

    result = runner.invoke(args=["import-plan", str(workbook)])

    assert result.exit_code == 0, result.output
    assert "plan_versions_created: 1" in result.output
    assert runner.invoke(args=["import-plan", str(workbook)]).exit_code != 0
