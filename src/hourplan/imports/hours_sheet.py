"""Reads the "one row per task, one column per day" hours sheets and unpivots them."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from hourplan.imports.mapping import Mapping, normalise

FIELDS = ("resource", "type", "code", "title", "phase", "application", "task_type", "description")
REQUIRED = ("resource", "type", "code", "description")


@dataclass(frozen=True)
class DayHours:
    day: date
    hours: Decimal
    column: int


@dataclass
class SheetRow:
    number: int
    values: dict[str, str]
    days: list[DayHours] = field(default_factory=list)
    problems: list[tuple[str, str]] = field(default_factory=list)  # (issue code, message)


@dataclass
class SheetLayout:
    fields: dict[str, int]  # field -> column index
    days: dict[int, date]  # column index -> day


def detect_layout(header: tuple[Any, ...], mapping: Mapping) -> SheetLayout | None:
    """Column positions, or None when the sheet is not an hours sheet."""
    fields: dict[str, int] = {}
    for index, cell in enumerate(header):
        name = mapping.columns.get(normalise(cell))
        if name and name not in fields:
            fields[name] = index
    if any(name not in fields for name in REQUIRED):
        return None
    days = _daily_columns(header)
    return SheetLayout(fields, days) if len(days) >= mapping.min_day_columns else None


def _daily_columns(header: tuple[Any, ...]) -> dict[int, date]:
    """Date headers minus the monthly-total block (first-of-month dates in rising order)."""
    dated = [(i, c.date() if isinstance(c, datetime) else c) for i, c in enumerate(header)]
    dated = [(i, c) for i, c in dated if isinstance(c, date)]
    skip = 0
    previous: date | None = None
    for _, day in dated:
        if day.day != 1 or (previous and (day.year, day.month) <= (previous.year, previous.month)):
            break
        previous = day
        skip += 1
    return dict(dated[skip:])


def parse_hours(value: object, quarter: Decimal) -> tuple[Decimal | None, str | None]:
    """Hours of a day cell. Empty/zero -> (None, None); unusable -> (None, message)."""
    text = str(value).replace("\xa0", " ").strip() if value is not None else ""
    if not text or isinstance(value, bool):
        return None, None
    try:
        hours = Decimal(text.replace(",", "."))
    except InvalidOperation:
        return None, f"Valor que no es un número de horas: {str(value)[:30]!r}"
    if hours == 0:
        return None, None
    if hours < 0 or hours > 24:
        return None, f"Horas fuera de rango (0-24): {hours}"
    rounded = (hours / quarter).quantize(Decimal(1)) * quarter
    if rounded != hours:
        return rounded or None, f"Horas {hours} redondeadas a {rounded} (múltiplos de {quarter})"
    return hours, None


def read_rows(
    rows: Iterator[tuple[Any, ...]], layout: SheetLayout, mapping: Mapping, first_number: int = 2
) -> Iterator[SheetRow]:
    """Rows that have text or hours. ``rows`` starts right after the header."""
    for number, raw in enumerate(rows, start=first_number):
        values = {
            name: ("" if idx >= len(raw) or raw[idx] is None else str(raw[idx]))
            for name, idx in layout.fields.items()
        }
        row = SheetRow(number, values)
        for column, day in layout.days.items():
            if column >= len(raw):
                break
            hours, problem = parse_hours(raw[column], mapping.quarter)
            if problem:
                row.problems.append(("bad_hours", f"{day.isoformat()}: {problem}"))
            if hours is not None:
                row.days.append(DayHours(day, hours, column))
        if row.days or row.problems:
            yield row
