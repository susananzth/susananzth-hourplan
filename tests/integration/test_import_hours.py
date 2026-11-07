from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from flask import Flask
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.catalogs.models import Application, CatalogItem
from hourplan.imports import services
from hourplan.imports.models import BatchStatus, ImportIssue
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.timesheet.models import Absence, TimeEntry

TEXT = (
    "Recurso",
    "Tipo",
    "Codigo",
    "Título -  PRY/TK",
    "Fase",
    "Aplicación",
    "Tipo Tarea",
    "Descripción",
)
MONTHLY = [datetime(2026, m, 1) for m in range(1, 13)]
DAYS = [datetime(2026, 1, d) for d in range(2, 32)] + [datetime(2026, 2, d) for d in range(1, 15)]
FIRST_DAY = len(TEXT) + len(MONTHLY)


def row(text: tuple[str, ...], hours: dict[int, object]) -> list[object]:
    cells: list[object] = [*text, *([None] * (len(MONTHLY) + len(DAYS)))]
    for index, value in hours.items():
        cells[FIRST_DAY + index] = value
    return cells


def make_book(path: Path, rows: list[list[object]]) -> Path:
    book = Workbook()
    sheet = book.active
    sheet.title = "Ana"
    sheet.append([*TEXT, *MONTHLY, *DAYS])
    for cells in rows:
        sheet.append(cells)
    other = book.create_sheet("Resumen")
    other.append(["Total", "Mes"])
    book.save(path)
    return path


@pytest.fixture
def workbook(tmp_path: Path) -> Path:
    return make_book(
        tmp_path / "horas.xlsx",
        [
            row(
                (
                    "Ana Pérez",
                    "Proyecto",
                    "PJ-00001NOTA",
                    "Portal",
                    "Desarrollo",
                    "APP-A",
                    "Codificación",
                    "Pantalla",
                ),
                {0: 8, 1: 4},
            ),
            row(
                (
                    "Ana Pérez",
                    "Proyecto",
                    "PJ-00001-NOTA",
                    "Portal nuevo",
                    "Análisis",
                    "APP-A",
                    "Reunión",
                    "Daily",
                ),
                {2: 2},
            ),
            row(
                ("Ana Pérez", "Proyecto", "PJ-00002-F1", "Otro", "Desarrollo", "", "", ""),
                {3: 1.5},
            ),
            row(
                (
                    "Ana Pérez",
                    "Ticket",
                    "#17733, #17734",
                    "Error",
                    "Desarrollo",
                    "APP-A",
                    "Codificación",
                    "Fix",
                ),
                {4: 3},
            ),
            row(("Ana Pérez", "Ticket", "Tickets", "Varios", "", "", "", ""), {5: 1}),
            row(("Ana Pérez", "Proyecto", "N/A", "", "", "", "", ""), {6: 0.3}),
            row(("Ana Pérez", "Onboarding", "", "Equipo", "Otros", "", "", ""), {7: 8}),
            row(("Ana Pérez", "Vacaciones", "", "", "", "", "", ""), {8: 8, 9: 8}),
            row(("", "Proyecto", "PJ-00001", "", "", "", "", ""), {10: 8}),
        ],
    )


def count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def codes(session: Session, batch_id: int) -> dict[str, int]:
    rows = session.execute(
        select(ImportIssue.code, func.count())
        .where(ImportIssue.batch_id == batch_id)
        .group_by(ImportIssue.code)
    )
    return dict(rows.all())


def test_import_stores_entries_and_reports_issues(session: Session, workbook: Path) -> None:
    batch = services.import_hours(session, workbook)

    assert batch.stats["sheets"] == 1
    assert batch.stats["entries"] == 8
    assert batch.stats["absences"] == 2
    assert count(session, TimeEntry) == 8
    assert count(session, Absence) == 2
    projects = {p.code: p for p in session.scalars(select(Project))}
    assert set(projects) == {"PJ-00001", "PJ-00002", "PJ-00002-F1", "17733"}
    assert projects["PJ-00002-F1"].parent_id == projects["PJ-00002"].id
    assert projects["PJ-00001"].title == "Portal"
    assert count(session, Resource) == 1
    assert count(session, Application) == 1
    assert codes(session, batch.id) == {
        "sheet_skipped": 1,
        "title_mismatch": 1,
        "multiple_tickets": 1,
        "invalid_ticket_code": 1,
        "invalid_project_code": 1,
        "bad_hours": 1,
        "missing_resource": 1,
    }
    stats = batch.stats
    # What was read but neither stored as work nor as absence: the row without a resource.
    assert Decimal(stats["hours_read"]) - Decimal(stats["hours_stored"]) - Decimal(
        stats["hours_absence"]
    ) == Decimal(8)


def test_unassigned_hours_are_kept_in_a_bucket(session: Session, workbook: Path) -> None:
    services.import_hours(session, workbook)

    bucket = session.scalars(select(CatalogItem).where(CatalogItem.code == "UNASSIGNED")).one()
    hours = session.scalar(
        select(func.sum(TimeEntry.hours)).where(TimeEntry.activity_bucket_id == bucket.id)
    )
    assert hours == Decimal("1.25")


def test_same_content_is_not_imported_twice(session: Session, workbook: Path) -> None:
    services.import_hours(session, workbook)

    with pytest.raises(services.ImportRejectedError, match="ya se importó"):
        services.import_hours(session, workbook)


def test_rows_already_stored_are_skipped(session: Session, workbook: Path, tmp_path: Path) -> None:
    services.import_hours(session, workbook)
    extended = make_book(
        tmp_path / "mas.xlsx",
        [
            row(
                (
                    "Ana Pérez",
                    "Proyecto",
                    "PJ-00001",
                    "Portal",
                    "Desarrollo",
                    "APP-A",
                    "Codificación",
                    "Pantalla",
                ),
                {0: 8, 1: 4},
            ),
            row(
                (
                    "Ana Pérez",
                    "Proyecto",
                    "PJ-00001",
                    "Portal",
                    "Desarrollo",
                    "APP-A",
                    "Codificación",
                    "Nueva",
                ),
                {20: 6},
            ),
        ],
    )

    batch = services.import_hours(session, extended)

    assert batch.stats["entries"] == 1
    assert batch.stats["entries_skipped_existing"] == 2
    assert count(session, TimeEntry) == 9


def test_identical_rows_in_one_file_are_both_kept(session: Session, tmp_path: Path) -> None:
    same = ("Ana", "Proyecto", "PJ-00001", "Portal", "Desarrollo", "", "", "Pruebas")
    path = make_book(tmp_path / "dup.xlsx", [row(same, {0: 2}), row(same, {0: 2})])

    services.import_hours(session, path)

    assert session.scalar(select(func.sum(TimeEntry.hours))) == Decimal(4)


def test_revert_removes_everything_the_batch_created(session: Session, workbook: Path) -> None:
    before = {m: count(session, m) for m in (Project, Resource, Application, CatalogItem)}
    batch = services.import_hours(session, workbook)

    services.revert_batch(session, batch)

    assert batch.status == BatchStatus.REVERTED
    assert count(session, TimeEntry) == 0
    assert count(session, Absence) == 0
    assert {m: count(session, m) for m in before} == before
    with pytest.raises(services.ImportRejectedError):
        services.revert_batch(session, batch)
    # After a revert the same file can be imported again.
    assert services.import_hours(session, workbook).stats["entries"] == 8


def test_revert_keeps_records_used_by_other_data(session: Session, workbook: Path) -> None:
    batch = services.import_hours(session, workbook)
    resource = session.scalars(select(Resource)).one()
    project = session.scalars(select(Project).where(Project.code == "PJ-00001")).one()
    session.add(
        TimeEntry(
            resource_id=resource.id, work_date=date(2026, 3, 1), hours=1, project_id=project.id
        )
    )
    session.flush()

    services.revert_batch(session, batch)

    assert count(session, TimeEntry) == 1
    assert session.scalars(select(Project)).one().code == "PJ-00001"
    assert count(session, Resource) == 1


def test_workbook_without_hours_sheets_is_rejected(session: Session, tmp_path: Path) -> None:
    book = Workbook()
    book.active.append(["Total", "Mes"])
    path = tmp_path / "vacio.xlsx"
    book.save(path)

    with pytest.raises(services.ImportRejectedError, match="hojas de horas"):
        services.import_hours(session, path)


def test_corrupt_file_is_rejected(session: Session, tmp_path: Path) -> None:
    path = tmp_path / "roto.xlsx"
    path.write_bytes(b"not a workbook")

    with pytest.raises(services.ImportRejectedError, match="No se pudo leer"):
        services.import_hours(session, path)


def test_cli_import_report_and_revert(app: Flask, workbook: Path) -> None:
    runner = app.test_cli_runner()

    imported = runner.invoke(args=["import-hours", str(workbook)])
    assert imported.exit_code == 0, imported.output
    assert "entries: 8" in imported.output
    batch_id = int(imported.output.split()[1].rstrip(":"))

    report = runner.invoke(args=["import-report", str(batch_id)])
    assert "[warning] invalid_project_code: 1" in report.output

    again = runner.invoke(args=["import-hours", str(workbook)])
    assert again.exit_code != 0
    assert "ya se importó" in again.output

    assert runner.invoke(args=["import-revert", str(batch_id)]).exit_code == 0
    assert runner.invoke(args=["import-revert", str(batch_id)]).exit_code != 0
    assert runner.invoke(args=["import-report", "999"]).exit_code != 0
