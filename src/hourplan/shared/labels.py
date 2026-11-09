"""Names the user sees for fields, and Spanish messages for validation errors.

Services and views never show a column or variable name (``project_id``): they ask here.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from hourplan.shared.translate import N_, _

FIELD_LABELS: Mapping[str, str] = {
    # Time entries, templates and favourites.
    "work_date": N_("Fecha"),
    "hours": N_("Horas"),
    "project_id": N_("Proyecto"),
    "activity_bucket_id": N_("Actividad"),
    "project_phase_id": N_("Fase"),
    "task_type_id": N_("Tipo de tarea"),
    "application_id": N_("Aplicación"),
    "description": N_("Descripción"),
    "ai_assisted": N_("Asistido por IA"),
    # Projects and phases.
    "name": N_("Nombre"),
    "goal": N_("Objetivo"),
    "start_date": N_("Inicio"),
    "end_date": N_("Fin"),
    "epic_id": N_("Épica"),
    "sprint_id": N_("Sprint"),
    "code": N_("Código"),
    "title": N_("Título"),
    "portfolio_id": N_("Cartera"),
    "requirement_type_id": N_("Tipo de requerimiento"),
    "priority_id": N_("Prioridad"),
    "status_id": N_("Estado"),
    "phase_template_id": N_("Plantilla de fases"),
    "parent_id": N_("Proyecto padre"),
    "manager_id": N_("Gerente de proyecto"),
    "lead_id": N_("Líder técnico"),
    "base_effort_hours": N_("Esfuerzo base (h)"),
    "contingency_hours": N_("Contingencia (h)"),
    "planned_start": N_("Inicio previsto"),
    "planned_end": N_("Fin previsto"),
    "status": N_("Estado de la fase"),
    "resource_id": N_("Persona"),
    "submitted_on": N_("Fecha de envío"),
    "resolved_on": N_("Fecha de resolución"),
    "outcome": N_("Resultado"),
    "observations": N_("Observaciones"),
    "rework_category_id": N_("Categoría de origen"),
    "rework_reason_id": N_("Motivo de origen"),
    # Absences.
    "absence_type": N_("Tipo de ausencia"),
    "start_on": N_("Desde"),
    "end_on": N_("Hasta"),
    "hours_per_day": N_("Horas por día"),
    "notes": N_("Notas"),
    # Policies.
    "effective_from": N_("Vigente desde"),
    "standard_daily_hours": N_("Jornada diaria (h)"),
    "daily_warning_hours": N_("Aviso diario (h)"),
    "max_project_weeks": N_("Semanas máximas por proyecto"),
    "idle_minutes": N_("Minutos de inactividad"),
    "colors_enabled": N_("Colores activos"),
    "hours_tolerance_pct": N_("Tolerancia de horas (%)"),
    "hours_red_pct": N_("Límite rojo de horas (%)"),
    "dates_tolerance_pct": N_("Tolerancia de fechas (%)"),
    "dates_red_pct": N_("Límite rojo de fechas (%)"),
}


def label(field: str) -> str:
    """The user-facing name of ``field``; unknown fields are shown readable, never raw."""
    known = FIELD_LABELS.get(field)
    return _(known) if known else field.replace("_", " ").capitalize()


def _limit(error: Mapping[str, Any], key: str) -> str:
    return str(error.get("ctx", {}).get(key, "")).replace(".", ",")


_TEMPLATES: Mapping[str, str] = {
    "missing": N_("es obligatorio."),
    "greater_than": N_("debe ser mayor que {gt}."),
    "greater_than_equal": N_("debe ser al menos {ge}."),
    "less_than": N_("debe ser menor que {lt}."),
    "less_than_equal": N_("no puede ser mayor que {le}."),
    "string_too_long": N_("es demasiado largo (máximo {max_length} caracteres)."),
    "date_parsing": N_("no es una fecha válida."),
    "date_from_datetime_parsing": N_("no es una fecha válida."),
    "date_type": N_("no es una fecha válida."),
    "decimal_parsing": N_("no es un número válido."),
    "decimal_type": N_("no es un número válido."),
    "int_parsing": N_("no es un número entero válido."),
    "enum": N_("no es una opción válida."),
    "extra_forbidden": N_("no se esperaba."),
}


def validation_message(err: ValidationError) -> str:
    """One sentence per problem in the current language, naming the field by its label."""
    parts = []
    for item in err.errors():
        field = str(item["loc"][-1]) if item["loc"] else ""
        template = _(_TEMPLATES[item["type"]]) if item["type"] in _TEMPLATES else None
        if item["type"] == "value_error":
            # Our own validators already write the whole sentence.
            parts.append(_(str(item["msg"]).removeprefix("Value error, ")))
        elif template:
            ctx = {k: _limit(item, k) for k in ("gt", "ge", "lt", "le", "max_length")}
            parts.append(
                f"{label(field)} {template.format(**ctx)}" if field else template.format(**ctx)
            )
        else:
            parts.append(_("%s no es válido.") % label(field) if field else _("Revisa los datos."))
    return " ".join(parts)
