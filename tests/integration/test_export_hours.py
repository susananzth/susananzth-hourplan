from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from flask import Flask
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.imports import export, services
from hourplan.imports.mapping import Mapping
from hourplan.timesheet.models import Absence, TimeEntry
from tests.integration.test_import_hours import make_book, row


@pytest.fixture
def source(tmp_path: Path) -> Path:
    return make_book(
        tmp_path / "horas.xlsx",
        [
            row(("Ana Pérez", "Proyecto", "PJ-00001", "Portal", "Desarrollo", "APP-A",
                 "Codificación", "Pantalla"), {0: 8, 1: 4}),
            row(("Ana Pérez", "Ticket", "17733", "Error", "", "", "", ""), {2: 2}),
            row(("Ana Pérez", "Reuniones", "", "", "", "", "", "Daily"), {3: 1.5}),
            row(("Ana Pérez", "Vacaciones", "", "", "", "", "", ""), {4: 8}),
        ],
    )  # fmt: skip


def totals(session: Session) -> tuple[Decimal, Decimal, int]:
    hours = session.scalar(select(func.sum(TimeEntry.hours))) or Decimal(0)
    absence = session.scalar(select(func.sum(Absence.hours_per_day))) or Decimal(0)
    return hours, absence, session.scalar(select(func.count(TimeEntry.id))) or 0


def test_export_can_be_imported_back_without_changes(
    app: Flask, session: Session, source: Path, tmp_path: Path
) -> None:
    services.import_hours(session, source)
    before = totals(session)
    out = tmp_path / "salida.xlsx"

    result = export.export_hours(session, out, Mapping())

    assert result["sheets"] == "1"
    assert Decimal(result["hours"]) == before[0]
    sheet = load_workbook(out).active
    assert sheet is not None
    assert sheet.title == "Ana Pérez"
    # Importing the export into an empty database gives the same hours.
    batch = services.import_hours(session, out)
    assert batch.stats["entries_skipped_existing"] == 4
    assert totals(session) == before


def test_range_limits_the_days(session: Session, source: Path, tmp_path: Path) -> None:
    services.import_hours(session, source)

    result = export.export_hours(
        session, tmp_path / "r.xlsx", Mapping(), start=date(2026, 1, 3), end=date(2026, 1, 3)
    )

    assert Decimal(result["hours"]) == Decimal(4)


def test_empty_period_is_rejected(session: Session, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No hay horas"):
        export.export_hours(session, tmp_path / "x.xlsx", Mapping())
