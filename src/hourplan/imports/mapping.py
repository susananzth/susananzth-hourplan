"""How a workbook's text becomes domain values. Pure functions, no database.

The defaults are neutral. A deployment tunes them with ``import_mapping.toml`` in the overlay.
"""

import re
import tomllib
import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Kind = Literal["project", "ticket", "bucket", "absence"]


class BacklogPhase(BaseModel):
    """Columns of one phase in the backlog sheet and the template step they fill."""

    step: str
    status: str
    start: str | None = None
    end: str | None = None


class BacklogMapping(BaseModel):
    sheet: str = "backlog"  # normalised sheet name
    # Header text (normalised) -> project field.
    columns: dict[str, str] = Field(
        default_factory=lambda: {
            "codigo": "code",
            "empresa": "portfolio",
            "titulo": "title",
            "status": "status",
            "prioridad": "priority",
            "esfuerzo (horas)": "effort",
            "presupuesto": "budget",
            "bitacora": "notes",
        }
    )
    phases: list[BacklogPhase] = Field(
        default_factory=lambda: [
            BacklogPhase(step="Análisis", status="st-def", start="ini-def", end="fin-def"),
            BacklogPhase(step="Diseño", status="st-ps", start="ini-ps", end="fin-ps"),
            BacklogPhase(step="Desarrollo", status="st-des", start="ini-des", end="fin-des"),
            BacklogPhase(step="Pruebas", status="st-qa", start="ini-qa", end="fin-qa"),
            BacklogPhase(step="Aceptación", status="st-uat", start="ini-uat", end="fin-uat"),
            BacklogPhase(step="Despliegue", status="stat-pase", end="f-pase"),
        ]
    )
    # Effort column (normalised) -> template step; several columns may add up on one step.
    efforts: dict[str, str] = Field(
        default_factory=lambda: {
            "esf-ps": "Diseño",
            "esf-dis": "Diseño",
            "esf-des": "Desarrollo",
            "esf-qa": "Pruebas",
            "esf-prd": "Despliegue",
        }
    )
    # Normalised phase status text -> PhaseStatus value.
    phase_statuses: dict[str, str] = Field(
        default_factory=lambda: {
            "completado": "done",
            "ok": "done",
            "desplegado": "done",
            "en curso": "in_progress",
            "pendiente": "pending",
        }
    )
    # Normalised project status text -> project_status catalog code (others are created).
    statuses: dict[str, str] = Field(
        default_factory=lambda: {
            "backlog": "BACKLOG",
            "en analisis": "IN_PROGRESS",
            "en desarrollo": "IN_PROGRESS",
            "en qa": "IN_PROGRESS",
            "en produccion": "DONE",
            "despriorizado": "ON_HOLD",
            "desestimado": "CANCELLED",
        }
    )
    # Normalised priority text -> priority catalog code (others are created).
    priorities: dict[str, str] = Field(
        default_factory=lambda: {"alta": "HIGH", "media": "MEDIUM", "baja": "LOW"}
    )


class PlanMapping(BaseModel):
    sheet: str = "planning"  # normalised sheet name
    # Normalised phase text -> template step name; "" = the project, no particular phase.
    phases: dict[str, str] = Field(
        default_factory=lambda: {
            "analisis": "Análisis",
            "desarrollo": "Desarrollo",
            "lev.obs": "Desarrollo",
            "sop.qa": "Pruebas",
            "sop.desp": "Despliegue",
            "otros": "",
        }
    )
    # Activity bucket for planned tickets that have no ticket number yet.
    ticket_bucket: str = "Atención de tickets"
    # Year of "2-Ene" style day headers when the sheet does not say it ("Ene-26").
    year: int | None = None
    # Planned hours per resource and day above this are reported as over-allocation.
    workday_hours: Decimal = Decimal(8)


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
    # Named groups: ``number``, optional ``year`` and ``variant`` (a split project: F1, F2...).
    project_code: str = r"PJ-?(?P<number>\d{3,5})(?!\d)(?:-(?P<variant>F\d+)\b)?"
    # How the normalised code is written; ``number`` is zero-padded to ``number_width``.
    project_code_format: str = "PJ-{number}"
    number_width: int = 5
    # Hours must be multiples of this step (0.25 by design).
    quarter: Decimal = Decimal("0.25")
    # A sheet is read as an hours sheet when it has at least this many date columns.
    min_day_columns: int = 28
    skip_sheets: list[str] = Field(default_factory=list)
    # Normalised name in a file -> name of the resource it is ("victor santos" = "Victor S. S.").
    resource_aliases: dict[str, str] = Field(default_factory=dict)

    # Phase template given to imported projects that have none (code; empty = the default one).
    phase_template: str = ""
    backlog: BacklogMapping = Field(default_factory=BacklogMapping)
    plan: PlanMapping = Field(default_factory=PlanMapping)

    @field_validator("resource_aliases")
    @classmethod
    def _normalise_aliases(cls, aliases: dict[str, str]) -> dict[str, str]:
        return {normalise(alias): name for alias, name in aliases.items()}


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


def parse_project_code(raw: object, mapping: Mapping) -> tuple[str, str | None] | None:
    """``(code, variant)`` such as ``("PJ-00032-F1", "F1")``, or None if it is not one."""
    match = re.search(mapping.project_code, clean(raw).upper())
    if not match:
        return None
    groups = match.groupdict()
    code = mapping.project_code_format.format(
        number=groups["number"].zfill(mapping.number_width), year=groups.get("year") or ""
    )
    variant = groups.get("variant")
    return (f"{code}-{variant}" if variant else code), variant


def parse_ticket_codes(raw: object) -> list[str]:
    """Numeric ticket ids in a cell: ``'#12, #34'`` -> ``['12', '34']``."""
    return re.findall(r"\d{3,}", clean(raw))
