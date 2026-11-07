from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hourplan.imports.hours_sheet import detect_layout, parse_hours, read_rows
from hourplan.imports.mapping import (
    Mapping,
    load_mapping,
    normalise,
    parse_project_code,
    parse_ticket_codes,
)

MAPPING = Mapping()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PJ-00001NOTA", ("PJ-00001", None)),
        ("PJ-00001-NOTA", ("PJ-00001", None)),
        (" pj-115", ("PJ-00115", None)),
        ("PJ-00032-F1", ("PJ-00032-F1", "F1")),
        ("PS - PJ-00007 - Notas", ("PJ-00007", None)),
    ],
)
def test_project_code_is_normalised(raw: str, expected: tuple[str, str | None]) -> None:
    assert parse_project_code(raw, MAPPING) == expected


@pytest.mark.parametrize("raw", ["PJ-99999999", "115", "N/A", "Portal Cliente", None, ""])
def test_not_a_project_code(raw: str | None) -> None:
    assert parse_project_code(raw, MAPPING) is None


def test_project_code_format_comes_from_the_mapping() -> None:
    custom = Mapping(
        project_code=r"PX-?(?P<number>\d{3,4})-(?P<year>\d{4})(?:-(?P<variant>F\d+)\b)?",
        project_code_format="PX-{number}-{year}",
        number_width=4,
    )

    assert parse_project_code("px-032-2026", custom) == ("PX-0032-2026", None)
    assert parse_project_code("PX-0032-2026-F2", custom) == ("PX-0032-2026-F2", "F2")
    assert parse_project_code("PX-0032", custom) is None


def test_ticket_codes() -> None:
    assert parse_ticket_codes("#17733 , #17734") == ["17733", "17734"]
    assert parse_ticket_codes("17733\xa0") == ["17733"]
    assert parse_ticket_codes("Tickets") == []


def test_normalise() -> None:
    assert normalise("  Análisis\xa0DE  Datos ") == "analisis de datos"
    assert normalise(None) == ""


@pytest.mark.parametrize(
    ("value", "hours", "warns"),
    [
        (None, None, False),
        ("\xa0", None, False),
        (0, None, False),
        (8, Decimal(8), False),
        ("1,5", Decimal("1.5"), False),
        (0.3, Decimal("0.25"), True),
        (25, None, True),
        (-1, None, True),
        ("abc", None, True),
    ],
)
def test_parse_hours(value: object, hours: Decimal | None, warns: bool) -> None:
    result, problem = parse_hours(value, Decimal("0.25"))
    assert result == hours
    assert bool(problem) is warns


def header(months: int = 3, days: int = 30) -> tuple[object, ...]:
    text = ("Recurso", "Tipo", "Codigo", "Fase", "Descripción")
    monthly = tuple(datetime(2026, m, 1) for m in range(1, months + 1))
    daily = tuple(datetime(2026, 1, d) for d in range(1, days + 1))
    return (*text, *monthly, *daily)


def test_layout_skips_the_monthly_block() -> None:
    layout = detect_layout(header(), Mapping())
    assert layout is not None
    assert min(layout.days) == 8  # 5 text columns + 3 monthly ones
    assert layout.days[8] == date(2026, 1, 1)
    assert layout.fields["description"] == 4


def test_sheet_without_day_columns_is_not_hours() -> None:
    assert detect_layout(header(days=5), Mapping()) is None
    assert detect_layout(("Recurso", "Tipo"), Mapping()) is None


def test_read_rows_unpivots_days() -> None:
    mapping = Mapping()
    layout = detect_layout(header(), mapping)
    assert layout is not None
    cells = ["Ana", "Ticket", "123", "Desarrollo", "Algo", 8, 0, 0] + [None] * 30
    cells[8], cells[9] = 4, 2
    rows = list(read_rows(iter([tuple(cells), (None,) * 38]), layout, mapping))
    assert len(rows) == 1
    assert [(d.day.day, d.hours) for d in rows[0].days] == [(1, 4), (2, 2)]
    assert rows[0].number == 2


def test_mapping_can_be_overridden_from_a_toml_file(tmp_path: Path) -> None:
    path = tmp_path / "import_mapping.toml"
    path.write_text('min_day_columns = 10\n[kinds]\nactividad = "bucket"\n', encoding="utf-8")

    mapping = load_mapping(path)

    assert mapping.min_day_columns == 10
    assert mapping.kinds == {"actividad": "bucket"}
    assert load_mapping(None) == Mapping()
    assert load_mapping(tmp_path / "missing.toml") == Mapping()
