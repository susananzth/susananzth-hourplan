"""Portfolio screens: list, create and edit projects, adjust phase dates and effort."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from pydantic import ValidationError
from sqlalchemy import select
from werkzeug.wrappers import Response

from hourplan.auth.models import User
from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.catalogs import services as catalogs
from hourplan.catalogs.models import CatalogKind
from hourplan.extensions import db
from hourplan.projects import cycles, services
from hourplan.projects.methodology import PROFILES, has_agile, profile_of
from hourplan.projects.models import (
    CycleOutcome,
    PhaseCycle,
    PhaseStatus,
    PhaseTemplate,
    ProjectPhase,
)
from hourplan.shared.i18n import N_, _, translated
from hourplan.shared.labels import label, validation_message

bp = Blueprint("projects", __name__, url_prefix="/proyectos", template_folder="templates")

VIEWERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER, Role.OBSERVER)
EDITORS = (Role.ADMIN, Role.PM, Role.LEAD)
PHASE_STATUS_LABELS = {
    PhaseStatus.PENDING: N_("Pendiente"),
    PhaseStatus.IN_PROGRESS: N_("En curso"),
    PhaseStatus.DONE: N_("Terminada"),
    PhaseStatus.SKIPPED: N_("Omitida"),
}
OUTCOME_LABELS = {
    CycleOutcome.PENDING: N_("Pendiente"),
    CycleOutcome.RETURNED: N_("Observada"),
    CycleOutcome.APPROVED: N_("Aprobada"),
}


def _today() -> date:
    return datetime.now().astimezone().date()


def _int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def _hours(raw: str | None, field: str) -> Decimal | None:
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValueError(
            _("%s: escribe un número, por ejemplo 40 o 12,5.") % (label(field),)
        ) from None


def _day(raw: str | None, field: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(_("%s: la fecha no es válida.") % (label(field),)) from None


def _error(err: Exception) -> str:
    if isinstance(err, ValidationError):
        return validation_message(err)
    return str(err)


def _project_data() -> services.ProjectData:
    form = request.form
    return services.ProjectData(
        code=form.get("codigo", ""),
        title=form.get("titulo", ""),
        portfolio_id=_int(form.get("cartera")),
        requirement_type_id=_int(form.get("tipo")),
        priority_id=_int(form.get("prioridad")),
        status_id=_int(form.get("estado")),
        phase_template_id=_int(form.get("plantilla")),
        parent_id=_int(form.get("padre")),
        manager_id=_int(form.get("gerente")),
        lead_id=_int(form.get("lider")),
        base_effort_hours=_hours(form.get("esfuerzo"), "base_effort_hours"),
        contingency_hours=_hours(form.get("contingencia"), "contingency_hours"),
        notes=form.get("notas"),
        methodology=form.get("metodologia") or None,  # type: ignore[arg-type]
    )


def _choices() -> dict[str, Any]:
    session = db.session
    return {
        "portfolios": [p for p in catalogs.list_portfolios(session) if p.is_active],
        "requirement_types": catalogs.list_items(session, CatalogKind.REQUIREMENT_TYPE),
        "priorities": catalogs.list_items(session, CatalogKind.PRIORITY),
        "statuses": catalogs.list_items(session, CatalogKind.PROJECT_STATUS),
        "templates": list(
            session.scalars(
                select(PhaseTemplate).where(PhaseTemplate.is_active).order_by(PhaseTemplate.name)
            )
        ),
        "users": list(
            session.scalars(select(User).where(User.is_active).order_by(User.display_name))
        ),
        "profiles": PROFILES,
        "parents": services.list_projects(session, include_closed=True),
    }


@bp.get("")
@roles_required(*VIEWERS)
def index() -> str:
    session = db.session
    portfolio_id = _int(request.args.get("cartera"))
    status_id = _int(request.args.get("estado"))
    closed = request.args.get("cerrados") == "1"
    query = request.args.get("q", "")
    return render_template(
        "projects/index.html",
        projects=services.list_projects(
            session,
            query=query,
            portfolio_id=portfolio_id,
            status_id=status_id,
            include_closed=closed,
        ),
        portfolios={p.id: p.name for p in catalogs.list_portfolios(session)},
        statuses=catalogs.list_items(session, CatalogKind.PROJECT_STATUS),
        status_names={
            i.id: i.name for i in catalogs.list_items(session, CatalogKind.PROJECT_STATUS)
        },
        filters={"q": query, "cartera": portfolio_id, "estado": status_id, "cerrados": closed},
        can_edit=current_user.has_role(*EDITORS),
    )


@bp.route("/nuevo", methods=["GET", "POST"])
@roles_required(*EDITORS)
def create() -> str | Response:
    if request.method == "POST":
        try:
            project = services.create_project(
                db.session, _project_data(), actor_id=int(current_user.id)
            )
        except (ValueError, ValidationError) as err:
            db.session.rollback()
            flash(_error(err), "error")
        else:
            flash("Proyecto creado con las fases de su plantilla.", "info")
            return redirect(url_for("projects.detail", project_id=project.id))
    return render_template("projects/form.html", project=None, form=request.form, **_choices())


@bp.get("/<int:project_id>")
@roles_required(*VIEWERS)
def detail(project_id: int) -> str:
    try:
        project = services.get_project(db.session, project_id)
    except services.ProjectError:
        abort(404)
    items = {
        i.id: i.name
        for kind in (CatalogKind.PROJECT_STATUS, CatalogKind.PRIORITY, CatalogKind.REQUIREMENT_TYPE)
        for i in catalogs.list_items(db.session, kind)
    }
    return render_template(
        "projects/detail.html",
        project=project,
        items=items,
        warning=services.duration_warning(db.session, project, _today()),
        span=services.planned_span(project),
        phase_statuses=translated(PHASE_STATUS_LABELS),
        gated=[p for p in project.phases if cycles.is_gated(p)],
        round_name=cycles.round_name,
        returned=cycles.returned_count(db.session, project.id),
        outcomes=translated(OUTCOME_LABELS),
        categories=catalogs.list_items(db.session, CatalogKind.REWORK_CATEGORY),
        reasons=catalogs.list_items(db.session, CatalogKind.REWORK_REASON),
        items_all={
            i.id: i.name
            for kind in (CatalogKind.REWORK_CATEGORY, CatalogKind.REWORK_REASON)
            for i in catalogs.list_items(db.session, kind)
        },
        today=_today(),
        can_edit=current_user.has_role(*EDITORS),
        profile=profile_of(project.methodology),
        agile=has_agile(project.methodology),
    )


@bp.route("/<int:project_id>/editar", methods=["GET", "POST"])
@roles_required(*EDITORS)
def edit(project_id: int) -> str | Response:
    try:
        project = services.get_project(db.session, project_id)
    except services.ProjectError:
        abort(404)
    if request.method == "POST":
        try:
            services.update_project(
                db.session, project_id, _project_data(), actor_id=int(current_user.id)
            )
        except (ValueError, ValidationError) as err:
            db.session.rollback()
            flash(_error(err), "error")
        else:
            flash("Proyecto actualizado.", "info")
            return redirect(url_for("projects.detail", project_id=project_id))
    return render_template("projects/form.html", project=project, form=request.form, **_choices())


@bp.post("/<int:project_id>/fases/<int:phase_id>")
@roles_required(*EDITORS)
def update_phase(project_id: int, phase_id: int) -> Response:
    form = request.form
    try:
        data = services.PhaseData(
            status=form.get("estado", ""),  # type: ignore[arg-type]
            planned_start=_day(form.get("inicio"), "planned_start"),
            planned_end=_day(form.get("fin"), "planned_end"),
            base_effort_hours=_hours(form.get("esfuerzo"), "base_effort_hours"),
            contingency_hours=_hours(form.get("contingencia"), "contingency_hours"),
        )
        phase = db.session.get(ProjectPhase, phase_id)
        if phase is None or phase.project_id != project_id:
            abort(404)
        services.update_phase(db.session, phase_id, data, actor_id=int(current_user.id))
    except (ValueError, ValidationError) as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash("Fase actualizada.", "info")
    return redirect(url_for("projects.detail", project_id=project_id))


@bp.post("/<int:project_id>/fases/<int:phase_id>/rondas")
@roles_required(*EDITORS)
def submit_round(project_id: int, phase_id: int) -> Response:
    phase = db.session.get(ProjectPhase, phase_id)
    if phase is None or phase.project_id != project_id:
        abort(404)
    try:
        day = _day(request.form.get("fecha"), "submitted_on")
        if day is None:
            raise ValueError(_("%s: indica la fecha.") % (label("submitted_on"),))
        cycles.submit(db.session, phase_id, day, actor_id=int(current_user.id))
    except ValueError as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash("Ronda enviada.", "info")
    return redirect(url_for("projects.detail", project_id=project_id, _anchor="rondas"))


@bp.post("/<int:project_id>/rondas/<int:cycle_id>")
@roles_required(*EDITORS)
def decide_round(project_id: int, cycle_id: int) -> Response:
    cycle = db.session.get(PhaseCycle, cycle_id)
    if cycle is None or cycle.phase.project_id != project_id:
        abort(404)
    form = request.form
    try:
        day = _day(form.get("fecha"), "resolved_on")
        if day is None:
            raise ValueError(_("%s: indica la fecha.") % (label("resolved_on"),))
        data = cycles.Decision(
            outcome=form.get("resultado", ""),  # type: ignore[arg-type]
            resolved_on=day,
            observations=form.get("observaciones"),
            rework_category_id=_int(form.get("categoria")),
            rework_reason_id=_int(form.get("motivo")),
        )
        cycles.decide(db.session, cycle_id, data, decider_id=int(current_user.id))
    except (ValueError, ValidationError) as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash("Ronda resuelta.", "info")
    return redirect(url_for("projects.detail", project_id=project_id, _anchor="rondas"))
