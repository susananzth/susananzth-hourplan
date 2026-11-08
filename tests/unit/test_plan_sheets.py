from datetime import date, datetime
from decimal import Decimal

from hourplan.imports.hours_sheet import DayHours
from hourplan.imports.mapping import Mapping
from hourplan.imports.plan_sheets import (
    Run,
    as_date,
    as_hours,
    day_columns,
    find_header,
    runs,
    sheet_year,
)


def day(d: date, hours: str) -> DayHours:
    return DayHours(d, Decimal(hours), 0)


def test_day_columns_skip_the_monthly_block_and_read_dates_excel_made() -> None:
    # Monthly totals ("Feb-26" became 26 Feb), then days; "2-Feb" became a date of another year.
    header = ["Recurso", "Ene-26", datetime(2026, 2, 26), "30-Dic", "2-Ene", datetime(2020, 2, 2)]

    assert day_columns(header, 2025) == {
        3: date(2025, 12, 30),
        4: date(2026, 1, 2),
        5: date(2026, 2, 2),
    }


def test_day_columns_ignore_unknown_months_and_impossible_days() -> None:
    assert day_columns(["2-Xyz", "31-Feb", "3-Mar"], 2026) == {2: date(2026, 3, 3)}


def test_sheet_year_comes_from_a_month_header() -> None:
    assert sheet_year([[None, "ENERO"], ["Recurso", "Ene-26"]]) == 2026
    assert sheet_year([["Abr-2027"]]) == 2027
    assert sheet_year([["Recurso", "2-Ene"]]) is None


def test_runs_split_on_gaps_hour_changes_and_skipped_weekdays() -> None:
    friday, monday, tuesday = date(2026, 1, 9), date(2026, 1, 12), date(2026, 1, 13)
    thursday = date(2026, 1, 15)

    result = runs([day(tuesday, "8"), day(friday, "8"), day(monday, "8"), day(thursday, "4")])

    assert result == [Run(friday, tuesday, Decimal(8), 3), Run(thursday, thursday, Decimal(4), 1)]
    assert result[0].hours == Decimal(24)
    assert len(runs([day(monday, "8"), day(tuesday, "2")])) == 2


def test_cell_readers_tell_empty_from_unreadable() -> None:
    assert as_date(datetime(2026, 1, 2, 9)) == (date(2026, 1, 2), True)
    assert as_date("\xa0") == (None, True)
    assert as_date("28-.11") == (None, False)
    assert as_hours(None) == (None, True)
    assert as_hours(12.5) == (Decimal("12.5"), True)
    assert as_hours("40") == (None, False)
    assert as_hours(-1) == (None, False)


def test_find_header_scans_the_first_rows() -> None:
    rows = [[None, "DEF"], [None, "Código", "Título"], ["PJ-00001", "Portal"]]

    assert find_header(rows, Mapping().backlog.columns, ("code", "title")) == (
        1,
        {"code": 1, "title": 2},
    )
    assert find_header(rows, Mapping().backlog.columns, ("code", "status")) is None


def test_resource_aliases_are_normalised() -> None:
    mapping = Mapping.model_validate({"resource_aliases": {"  Víctor  SANTOS": "Victor S."}})

    assert mapping.resource_aliases == {"victor santos": "Victor S."}
