"""Readers for the backlog and planning sheets. Pure functions, no database."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from hourplan.imports.hours_sheet import DayHours
from hourplan.imports.mapping import normalise

MONTHS = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6, "jul": 7, "ago": 8,
    "set": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
    "jan": 1, "apr": 4, "aug": 8, "dec": 12,
}  # fmt: skip
_DAY = re.compile(r"^(\d{1,2})-([a-z]{3})$")
_MONTH = re.compile(r"^([a-z]{3})-(\d{2}|\d{4})$")

Row = Sequence[Any]


def find_header(
    rows: Sequence[Row], columns: dict[str, str], required: Sequence[str], scan: int = 10
) -> tuple[int, dict[str, int]] | None:
    """Index of the first row (among the first ``scan``) that has every required column."""
    for index, row in enumerate(rows[:scan]):
        fields: dict[str, int] = {}
        for position, cell in enumerate(row):
            name = columns.get(normalise(cell))
            if name and name not in fields:
                fields[name] = position
        if all(name in fields for name in required):
            return index, fields
    return None


def header_positions(row: Row) -> dict[str, int]:
    """Normalised header text -> first column with it."""
    positions: dict[str, int] = {}
    for position, cell in enumerate(row):
        positions.setdefault(normalise(cell), position)
    return positions


def sheet_year(rows: Sequence[Row]) -> int | None:
    """Year written in a month header such as "Ene-26" in the given rows."""
    for row in rows:
        for cell in row:
            match = _MONTH.match(normalise(cell)) if isinstance(cell, str) else None
            if match and match.group(1) in MONTHS:
                year = int(match.group(2))
                return year + 2000 if year < 100 else year
    return None


def _day_month(cell: object) -> tuple[int, int] | None:
    """``(day, month)`` of a "2-Ene" header, or of a date Excel made from one ("2-Feb")."""
    if isinstance(cell, date):
        return cell.day, cell.month
    match = _DAY.match(normalise(cell)) if isinstance(cell, str) else None
    if match and match.group(2) in MONTHS:
        return int(match.group(1)), MONTHS[match.group(2)]
    return None


def day_columns(header: Row, year: int) -> dict[int, date]:
    """Day columns, dated from ``year`` on (the year rolls over when the months go back).

    The block starts at the first "2-Ene" style header; dates before it are monthly totals.
    Excel turns some of those headers into dates of an arbitrary year: only day and month count.
    """
    columns: dict[int, date] = {}
    previous: date | None = None
    started = False
    for position, cell in enumerate(header):
        started = started or (isinstance(cell, str) and _day_month(cell) is not None)
        parts = _day_month(cell) if started else None
        if parts is None:
            continue
        try:
            day = date(year, parts[1], parts[0])
            if previous and day < previous:
                year += 1
                day = date(year, parts[1], parts[0])
        except ValueError:
            continue
        columns[position] = day
        previous = day
    return columns


def as_date(value: object) -> tuple[date | None, bool]:
    """``(date, ok)``: empty cells are ``(None, True)``; unreadable ones ``(None, False)``."""
    if isinstance(value, datetime):
        return value.date(), True
    if isinstance(value, date):
        return value, True
    if value is None or not str(value).replace("\xa0", " ").strip():
        return None, True
    return None, False


def as_hours(value: object) -> tuple[Decimal | None, bool]:
    """Non-negative hours: empty is ``(None, True)``; text or negative is ``(None, False)``."""
    if value is None or not str(value).replace("\xa0", " ").strip():
        return None, True
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        return None, False
    hours = Decimal(str(value))
    return (hours, True) if hours >= 0 else (None, False)


@dataclass(frozen=True)
class Run:
    """Consecutive working days with the same planned hours each day."""

    start: date
    end: date
    hours_per_day: Decimal
    days: int

    @property
    def hours(self) -> Decimal:
        return self.hours_per_day * self.days


def _next_weekday(day: date) -> date:
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


def runs(days: Sequence[DayHours]) -> list[Run]:
    """Group days into runs; a gap, a change of hours or a skipped weekday starts a new run."""
    result: list[Run] = []
    for item in sorted(days, key=lambda d: d.day):
        last = result[-1] if result else None
        if last and last.hours_per_day == item.hours and _next_weekday(last.end) == item.day:
            result[-1] = Run(last.start, item.day, last.hours_per_day, last.days + 1)
        else:
            result.append(Run(item.day, item.day, item.hours, 1))
    return result
