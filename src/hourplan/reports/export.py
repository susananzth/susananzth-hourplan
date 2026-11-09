"""Excel exports of the report screens: same figures and filters, one sheet per table.

Text that starts with a formula character is stored as plain text so a project title cannot run
as a formula when the file is opened.
"""

from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy.orm import Session

from hourplan.reports import compare, dashboard, notes
from hourplan.reports.compare import Evaluation, PersonalLine, Summary
from hourplan.reports.dashboard import Row, Totals

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
STATUS_TEXT = {"green": "En meta", "amber": "Atención", "red": "Fuera de meta"}
FORMULA_STARTS = ("=", "+", "-", "@")
MAX_WIDTH = 60
BOLD = Font(bold=True)
HOURS = "0.00"
PERCENT = "0.0"


def _cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, str) and value.startswith(FORMULA_STARTS):
        return "'" + value
    return value


def _sheet(
    book: Workbook, title: str, headers: Sequence[str], rows: Iterable[Sequence[Any]]
) -> None:
    sheet: Worksheet = book.create_sheet(title)
    sheet.append(list(headers))
    for cell in sheet[1]:
        cell.font = BOLD
    for row in rows:
        sheet.append([_cell(v) for v in row])
    sheet.freeze_panes = "A2"
    for index, header in enumerate(headers, start=1):
        widest = max(
            (len(str(c.value)) for c in sheet[get_column_letter(index)] if c.value is not None),
            default=0,
        )
        sheet.column_dimensions[get_column_letter(index)].width = min(
            max(widest + 2, len(header) + 2), MAX_WIDTH
        )
        if (
            all(isinstance(c.value, float) for c in sheet[get_column_letter(index)][1:])
            and sheet.max_row > 1
        ):
            for cell in sheet[get_column_letter(index)][1:]:
                cell.number_format = HOURS


def _book() -> Workbook:
    book = Workbook()
    book.remove(book.worksheets[0])
    return book


def _bytes(book: Workbook) -> bytes:
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _summary_sheet(book: Workbook, start: date, end: date, totals: Totals, who: str | None) -> None:
    rows: list[tuple[str, Any]] = [
        ("Desde", start.isoformat()),
        ("Hasta", end.isoformat()),
        ("Horas registradas", totals.hours),
        ("Rework (horas)", totals.rework),
        ("Rework (% de las horas)", totals.rework_share),
        ("Absorbidas por contingencias (horas)", totals.contingency),
    ]
    if who:
        rows.insert(0, ("Persona", who))
    else:
        rows += [
            ("Proyectos cerrados en meta", totals.closed["green"]),
            ("Proyectos cerrados con atención", totals.closed["amber"]),
            ("Proyectos cerrados fuera de meta", totals.closed["red"]),
            ("Proyectos cerrados sin evaluación", totals.closed["none"]),
        ]
    _sheet(book, "Resumen", ("Dato", "Valor"), rows)


def _hours_rows(rows: list[Row]) -> list[tuple[Any, ...]]:
    return [(r.label, r.hours, r.initial, r.rework, r.rework_share, r.contingency) for r in rows]


HOURS_HEADERS = (
    "Concepto",
    "Horas",
    "Horas iniciales",
    "Rework (horas)",
    "Rework (%)",
    "Contingencias (horas)",
)


def panel_workbook(session: Session, start: date, end: date) -> bytes:
    """The hours dashboard: totals, one sheet per dimension and rework by origin."""
    book = _book()
    _summary_sheet(book, start, end, dashboard.totals(session, start, end), None)
    for dim, name in dashboard.DIMENSIONS.items():
        rows = dashboard.hours_by(session, dim, start, end)
        _sheet(book, name, (name, *HOURS_HEADERS[1:]), _hours_rows(rows))
    categories = compare.rework_by_category(session, start=start, end=end)
    _sheet(book, "Rework por origen", ("Categoría", "Horas"), categories)
    return _bytes(book)


def _evaluation_cells(ev: Evaluation) -> tuple[Any, ...]:
    return (
        ev.planned_hours,
        ev.actual_hours,
        ev.rework_hours,
        ev.hours_pct,
        STATUS_TEXT.get(ev.hours_status.value, "") if ev.hours_status else "",
        ev.dates_pct,
        STATUS_TEXT.get(ev.dates_status.value, "") if ev.dates_status else "",
    )


def projects_workbook(summaries: list[Summary]) -> bytes:
    """Plan against actual, one line per project (what ``/informes/proyectos`` shows)."""
    book = _book()
    rows = []
    for s in summaries:
        state = (
            f"Cerrado el {s.project.closed_on.isoformat()}" if s.project.closed_on else "Abierto"
        )
        plan = {"baseline": "Línea base", "current": "Última versión"}.get(s.plan_source or "", "")
        rows.append(
            (s.project.code, s.project.title, plan, *_evaluation_cells(s.evaluation), state)
        )
    headers = (
        "Código", "Proyecto", "Plan usado", "Planificado (h)", "Real (h)", "Rework (h)",
        "Desvío de horas (%)", "Estado de horas", "Desvío de fechas (%)", "Estado de fechas",
        "Estado del proyecto",
    )  # fmt: skip
    _sheet(book, "Plan contra real", headers, rows)
    return _bytes(book)


def personal_workbook(
    session: Session, resource_id: int, name: str, start: date, end: date
) -> bytes:
    """A person's own report: totals, plan against actual per project, hours per month and task."""
    book = _book()
    totals = dashboard.totals(session, start, end, resource_id=resource_id)
    _summary_sheet(book, start, end, totals, name)
    lines: list[PersonalLine] = compare.personal_lines(session, resource_id)
    _sheet(
        book,
        "Plan contra real",
        ("Código", "Proyecto", "Asignado (h)", "Real (h)", "Rework (h)"),
        [
            (p.project.code, p.project.title, p.planned_hours, p.actual_hours, p.rework_hours)
            for p in lines
        ],
    )
    for dim in ("mes", "tipo"):
        label = dashboard.DIMENSIONS[dim]
        rows = dashboard.hours_by(session, dim, start, end, resource_id=resource_id)
        _sheet(book, label, (label, *HOURS_HEADERS[1:]), _hours_rows(rows))
    _sheet(
        book,
        "Notas de desvío",
        ("Fecha", "Proyecto", "Nota"),
        [
            (n.created_at.date().isoformat(), p.code, n.body)
            for p, n in notes.notes_for_resource(session, resource_id)
        ],
    )
    return _bytes(book)
