import pytest
from pydantic import ValidationError

from hourplan.shared.labels import label, validation_message
from hourplan.timesheet.absences import AbsenceData
from hourplan.timesheet.services import EntryData


def message(**values: object) -> str:
    with pytest.raises(ValidationError) as caught:
        EntryData(**values)  # type: ignore[arg-type]
    return validation_message(caught.value)


def test_label_uses_the_table_and_falls_back_to_readable_text() -> None:
    assert label("project_id") == "Proyecto"
    assert label("some_new_field") == "Some new field"


def test_entry_errors_name_fields_by_label_never_by_column() -> None:
    both = message(work_date="2026-06-15", hours=1, project_id=1, activity_bucket_id=2)
    neither = message(work_date="2026-06-15", hours=1)

    assert both == neither == "Indica proyecto o actividad, pero no ambos."
    assert "_id" not in both


def test_standard_pydantic_errors_are_translated() -> None:
    assert message(work_date="2026-06-15", hours=0, project_id=1) == ("Horas debe ser mayor que 0.")
    assert message(work_date="2026-06-15", hours=25, project_id=1) == (
        "Horas no puede ser mayor que 24."
    )
    assert message(work_date="mala", hours=1, project_id=1) == "Fecha no es una fecha válida."
    assert message(hours=1, project_id=1) == "Fecha es obligatorio."
    assert message(work_date="2026-06-15", hours=0.1, project_id=1) == (
        "Horas debe ser múltiplo de 0,25."
    )


def test_absence_errors_use_labels() -> None:
    with pytest.raises(ValidationError) as caught:
        AbsenceData(absence_type="vacation", start_on="2026-07-02", end_on="2026-07-01")

    assert validation_message(caught.value) == "Hasta no puede ser anterior a desde."
