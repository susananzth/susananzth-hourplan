"""Names the user sees for fields, and Spanish messages for validation errors.

Services and views never show a column or variable name (``project_id``): they ask here.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

FIELD_LABELS: Mapping[str, str] = {
    # Time entries, templates and favourites.
    "work_date": "Fecha",
    "hours": "Horas",
    "project_id": "Proyecto",
    "activity_bucket_id": "Actividad",
    "project_phase_id": "Fase",
    "task_type_id": "Tipo de tarea",
    "application_id": "Aplicación",
    "description": "Descripción",
    "ai_assisted": "Asistido por IA",
    # Projects and phases.
    "code": "Código",
    "title": "Título",
    "portfolio_id": "Cartera",
    "requirement_type_id": "Tipo de requerimiento",
    "priority_id": "Prioridad",
    "status_id": "Estado",
    "phase_template_id": "Plantilla de fases",
    "parent_id": "Proyecto padre",
    "manager_id": "Gerente de proyecto",
    "lead_id": "Líder técnico",
    "base_effort_hours": "Esfuerzo base (h)",
    "contingency_hours": "Contingencia (h)",
    "planned_start": "Inicio previsto",
    "planned_end": "Fin previsto",
    "status": "Estado de la fase",
    "resource_id": "Persona",
    "submitted_on": "Fecha de envío",
    "resolved_on": "Fecha de resolución",
    "outcome": "Resultado",
    "observations": "Observaciones",
    "rework_category_id": "Categoría de origen",
    "rework_reason_id": "Motivo de origen",
    # Absences.
    "absence_type": "Tipo de ausencia",
    "start_on": "Desde",
    "end_on": "Hasta",
    "hours_per_day": "Horas por día",
    "notes": "Notas",
    # Policies.
    "effective_from": "Vigente desde",
    "standard_daily_hours": "Jornada diaria (h)",
    "daily_warning_hours": "Aviso diario (h)",
    "max_project_weeks": "Semanas máximas por proyecto",
    "idle_minutes": "Minutos de inactividad",
    "colors_enabled": "Colores activos",
    "hours_tolerance_pct": "Tolerancia de horas (%)",
    "hours_red_pct": "Límite rojo de horas (%)",
    "dates_tolerance_pct": "Tolerancia de fechas (%)",
    "dates_red_pct": "Límite rojo de fechas (%)",
}


def label(field: str) -> str:
    """The user-facing name of ``field``; unknown fields are shown readable, never raw."""
    return FIELD_LABELS.get(field) or field.replace("_", " ").capitalize()


def _limit(error: Mapping[str, Any], key: str) -> str:
    return str(error.get("ctx", {}).get(key, "")).replace(".", ",")


_TEMPLATES: Mapping[str, str] = {
    "missing": "es obligatorio.",
    "greater_than": "debe ser mayor que {gt}.",
    "greater_than_equal": "debe ser al menos {ge}.",
    "less_than": "debe ser menor que {lt}.",
    "less_than_equal": "no puede ser mayor que {le}.",
    "string_too_long": "es demasiado largo (máximo {max_length} caracteres).",
    "date_parsing": "no es una fecha válida.",
    "date_from_datetime_parsing": "no es una fecha válida.",
    "date_type": "no es una fecha válida.",
    "decimal_parsing": "no es un número válido.",
    "decimal_type": "no es un número válido.",
    "int_parsing": "no es un número entero válido.",
    "enum": "no es una opción válida.",
    "extra_forbidden": "no se esperaba.",
}


def validation_message(err: ValidationError) -> str:
    """One Spanish sentence per problem, naming the field by its label."""
    parts = []
    for item in err.errors():
        field = str(item["loc"][-1]) if item["loc"] else ""
        template = _TEMPLATES.get(item["type"])
        if item["type"] == "value_error":
            # Our own validators already write the whole sentence.
            parts.append(str(item["msg"]).removeprefix("Value error, "))
        elif template:
            ctx = {k: _limit(item, k) for k in ("gt", "ge", "lt", "le", "max_length")}
            parts.append(f"{label(field)} {template.format(**ctx)}" if field else template)
        else:
            parts.append(f"{label(field)} no es válido." if field else "Revisa los datos.")
    return " ".join(parts)
