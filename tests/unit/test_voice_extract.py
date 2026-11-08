from datetime import date
from decimal import Decimal

import pytest

from hourplan.voice import extract
from hourplan.voice.extract import Candidates, Option

TODAY = date(2026, 11, 9)  # a Monday

CANDIDATES = Candidates(
    projects=(
        Option(1, "Portal de clientes", "PJ-00001"),
        Option(2, "Migración de facturas", "PJ-00002"),
        Option(3, "Ticket de acceso", "TK-00001"),
    ),
    phases=(
        Option(10, "Análisis", project_id=1),
        Option(11, "Desarrollo", project_id=1),
        Option(12, "Pruebas", project_id=1),
        Option(20, "Análisis", project_id=2),
    ),
    buckets=(Option(30, "Reuniones internas"), Option(31, "Capacitación")),
    task_types=(Option(40, "Soporte"), Option(41, "Revisión de código")),
    applications=(Option(50, "Módulo de pagos", "APP-A"), Option(51, "Reportes", "APP-B")),
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("dos horas", "2"),
        ("2 horas", "2"),
        ("1,5 h", "1.5"),
        ("1.5 horas", "1.5"),
        ("una hora y media", "1.5"),
        ("hora y media", "1.5"),
        ("dos horas y media", "2.5"),
        ("tres horas y cuarto", "3.25"),
        ("media hora", "0.5"),
        ("un cuarto de hora", "0.25"),
        ("tres cuartos de hora", "0.75"),
        ("45 minutos", "0.75"),
        ("10 minutos", "0.25"),
        ("1 hora 20 minutos", "1.25"),
        ("trabajé ocho horas", "8"),
    ],
)
def test_hours(text: str, expected: str) -> None:
    hours, span = extract.parse_hours(extract.fold(text))
    assert hours == Decimal(expected)
    assert span is not None


def test_no_hours() -> None:
    assert extract.parse_hours("hable con el cliente") == (None, None)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hoy trabajé", date(2026, 11, 9)),
        ("ayer trabajé", date(2026, 11, 8)),
        ("anteayer trabajé", date(2026, 11, 7)),
        ("antes de ayer", date(2026, 11, 7)),
        ("el viernes", date(2026, 11, 6)),
        ("el lunes", date(2026, 11, 9)),
        ("el miércoles pasado", date(2026, 11, 4)),
        ("el 3 de noviembre", date(2026, 11, 3)),
        ("el 20 de diciembre", date(2025, 12, 20)),
        ("el 3 de noviembre de 2025", date(2025, 11, 3)),
        ("el 3/11", date(2026, 11, 3)),
    ],
)
def test_dates(text: str, expected: date) -> None:
    found, span = extract.parse_date(extract.fold(text), TODAY)
    assert found == expected
    assert span is not None


def test_code_is_heard_loosely() -> None:
    for spoken in ("pj-00001", "pj 00001", "pj 1", "PJ cero uno".lower()):
        found = extract.match_code(CANDIDATES.projects, extract.fold(spoken)).option
        if spoken.endswith("cero uno"):
            assert found is None  # words for digits are not understood
        else:
            assert found is not None
            assert found.id == 1


def test_code_does_not_match_longer_number() -> None:
    assert extract.match_code(CANDIDATES.projects, "pj 12").option is None


def test_simple_dictation() -> None:
    [line] = extract.extract(
        "Ayer trabajé dos horas y media en PJ-00001 en pruebas del módulo de pagos con IA",
        TODAY,
        CANDIDATES,
    )
    assert line.work_date == date(2026, 11, 8)
    assert line.hours == Decimal("2.5")
    assert line.project_id == 1
    assert line.project_phase_id == 12
    assert line.application_id == 50
    assert line.ai_assisted is True
    assert line.activity_bucket_id is None
    assert line.origins["project_id"] == "regla"
    assert line.notes == []


def test_title_instead_of_code_and_task_type() -> None:
    [line] = extract.extract("Una hora de soporte para el portal de clientes", TODAY, CANDIDATES)
    assert line.project_id == 1
    assert line.task_type_id == 40
    assert line.work_date == TODAY


def test_activity_bucket_without_project() -> None:
    [line] = extract.extract("Hoy dos horas de capacitación", TODAY, CANDIDATES)
    assert line.activity_bucket_id == 31
    assert line.project_id is None


def test_several_lines_inherit_the_date() -> None:
    lines = extract.extract(
        "El viernes dos horas en PJ-00001 análisis y luego 1 hora en PJ-00002, "
        "además 45 minutos de reuniones internas",
        TODAY,
        CANDIDATES,
    )
    assert [line.hours for line in lines] == [Decimal(2), Decimal(1), Decimal("0.75")]
    assert {line.work_date for line in lines} == {date(2026, 11, 6)}
    assert [line.project_id for line in lines] == [1, 2, None]
    assert lines[0].project_phase_id == 10
    assert lines[1].project_phase_id is None
    assert lines[2].activity_bucket_id == 30


def test_phase_is_taken_only_from_the_matched_project() -> None:
    [line] = extract.extract("dos horas en PJ-00002 desarrollo", TODAY, CANDIDATES)
    assert line.project_id == 2
    assert line.project_phase_id is None


def test_missing_pieces_are_reported() -> None:
    [line] = extract.extract("dos horas de algo raro", TODAY, CANDIDATES)
    assert line.hours == Decimal(2)
    assert "Falta el proyecto o la actividad." in line.notes
    [only_text] = extract.extract("hablé con el cliente", TODAY, CANDIDATES)
    assert only_text.hours is None
    assert "No se entendió la cantidad de horas." in only_text.notes


def test_future_date_is_flagged() -> None:
    [line] = extract.extract("el 20 de noviembre de 2026 dos horas en PJ-00001", TODAY, CANDIDATES)
    assert any("futura" in note for note in line.notes)


def test_description_drops_the_quantity_and_date() -> None:
    [line] = extract.extract("Ayer, 2 horas, corregí el formulario de pagos", TODAY, CANDIDATES)
    assert line.description == "corregí el formulario de pagos"


def test_empty_transcript() -> None:
    assert extract.extract("   ", TODAY, CANDIDATES) == []


def test_round_quarter() -> None:
    assert extract.round_quarter(Decimal("0.1")) == Decimal("0.25")
    assert extract.round_quarter(Decimal("1.1")) == Decimal("1.00")
    assert extract.round_quarter(Decimal("1.13")) == Decimal("1.25")
    assert extract.round_quarter(Decimal(0)) == Decimal(0)


def test_llm_values_are_checked_against_the_candidates() -> None:
    rules = extract.extract("dos horas en PJ-00001", TODAY, CANDIDATES)
    raw = [
        {
            "date": "2026-11-08",
            "hours": 2.1,
            "project_id": 999,  # unknown: falls back to the rules
            "phase_id": 12,
            "task_type_id": 41,
            "application_id": "x",
            "description": "Pruebas de regresión",
            "ai_assisted": True,
        }
    ]
    [line] = extract.merge_llm(rules, raw, TODAY, CANDIDATES)
    assert line.project_id == 1
    assert line.work_date == date(2026, 11, 8)
    assert line.hours == Decimal("2.00")
    assert line.task_type_id == 41
    assert line.application_id is None
    assert line.description == "Pruebas de regresión"
    assert line.origins["task_type_id"] == "llm"
    assert line.origins["project_id"] == "regla"
    assert line.ai_assisted is True


def test_llm_with_nothing_valid_keeps_the_rules() -> None:
    rules = extract.extract("dos horas en PJ-00001", TODAY, CANDIDATES)
    assert extract.merge_llm(rules, "basura", TODAY, CANDIDATES) is rules
    assert extract.merge_llm(rules, [{"hours": "x"}, 3], TODAY, CANDIDATES) is rules


def test_llm_with_another_line_count_replaces_the_rules() -> None:
    rules = extract.extract("dos horas en PJ-00001", TODAY, CANDIDATES)
    raw = [
        {"date": "2026-11-09", "hours": 1, "project_id": 1},
        {"date": "2026-11-09", "hours": 1, "bucket_id": 31},
    ]
    lines = extract.merge_llm(rules, raw, TODAY, CANDIDATES)
    assert [line.project_id for line in lines] == [1, None]
    assert lines[1].activity_bucket_id == 31
    assert any("otro número" in note for note in lines[0].notes)
