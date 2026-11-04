"""How a workbook's text becomes domain values. Pure functions, no database.

The defaults are neutral. A deployment tunes them with ``import_mapping.toml`` in the overlay.
"""

import re
import tomllib
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["project", "ticket", "bucket", "absence"]


class Mapping(BaseModel):
    # Header text (normalised) -> field, for the columns the sheet must or may have.
    columns: dict[str, str] = Field(
        default_factory=lambda: {
            "recurso": "resource",
            "tipo": "type",
            "codigo": "code",
            "titulo - pry/tk": "title",
            "fase": "phase",
            "aplicacion": "application",
            "apliacion": "application",
            "tipo tarea": "task_type",
            "descripcion": "description",
        }
    )
    # Normalised "Tipo" text -> kind. Any other text is an activity bucket named after it.
    kinds: dict[str, Kind] = Field(
        default_factory=lambda: dict[str, Kind](
            {
                "proyecto": "project",
                "ticket": "ticket",
                "vacaciones": "absence",
                "permiso": "absence",
                "licencias": "absence",
            }
        )
    )
    # Normalised "Tipo" text of an absence -> AbsenceType value.
    absences: dict[str, str] = Field(
        default_factory=lambda: {
            "vacaciones": "vacation",
            "permiso": "permission",
            "licencias": "sick_leave",
        }
    )
    # Group 1 = number, group 2 = year, optional group 3 = split variant (F1, F2...).
    project_code: str = r"(?:PROY-?)?(?<!\d)(\d{3,4})-(\d{4})(?:-(F\d+)\b)?"
    # Hours must be multiples of this step (0.25 by design).
    quarter: Decimal = Decimal("0.25")
    # A sheet is read as an hours sheet when it has at least this many date columns.
    min_day_columns: int = 28
    skip_sheets: list[str] = Field(default_factory=list)


def load_mapping(path: Path | None) -> Mapping:
    if path is None or not path.is_file():
        return Mapping()
    return Mapping.model_validate(tomllib.loads(path.read_text(encoding="utf-8")))


def normalise(value: object) -> str:
    """Casefolded, accent-free text with collapsed spaces; '' for empty cells."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value).replace("\xa0", " "))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.casefold().split())


def clean(value: object, limit: int | None = None) -> str:
    """Trimmed text with collapsed spaces, cut to ``limit``."""
    text = " ".join(str(value).replace("\xa0", " ").split()) if value is not None else ""
    return text[:limit] if limit else text


def slug(value: str, limit: int = 40) -> str:
    text = re.sub(r"[^A-Z0-9]+", "_", normalise(value).upper()).strip("_")
    return text[:limit] or "OTHER"


def parse_project_code(raw: object, pattern: str) -> tuple[str, str | None] | None:
    """``(code, variant)`` such as ``("PROY-0032-2026", "F1")``, or None if it is not one."""
    match = re.search(pattern, clean(raw).upper())
    if not match:
        return None
    number, year, variant = match.group(1), match.group(2), match.group(3)
    code = f"PROY-{number.zfill(4)}-{year}"
    return (f"{code}-{variant}" if variant else code), variant


def parse_ticket_codes(raw: object) -> list[str]:
    """Numeric ticket ids in a cell: ``'#12, #34'`` -> ``['12', '34']``."""
    return re.findall(r"\d{3,}", clean(raw))
