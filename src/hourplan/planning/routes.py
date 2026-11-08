"""Planning screens: a project's plan (versions, baseline, allocations) and team capacity."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from pydantic import ValidationError
from sqlalchemy import select
from werkzeug.wrappers import Response

from hourplan.audit.services import MissingReasonError
from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.catalogs import services as catalogs
from hourplan.extensions import db
from hourplan.planning import services
from hourplan.projects import services as projects
from hourplan.resources.models import Resource
from hourplan.shared.labels import label, validation_message

bp = Blueprint("planning", __name__, url_prefix="/planificacion", template_folder="templates")

VIEWERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER, Role.OBSERVER)
PLANNERS = (Role.ADMIN, Role.PM, Role.LEAD)
CAPACITY_VIEWERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.OBSERVER)
MAX_WEEKS = 26


def _today() -> date:
    return datetime.now().astimezone().date()


def _int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def _hours(raw: str | None) -> Decimal:
    raw = (raw or "").strip().replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValueError(f"{label('hours')}: escribe un número, por ejemplo 16 o 7,5.") from None


def _error(err: Exception) -> str:
    if isinstance(err, ValidationError):
        return validation_message(err)
    return str(err)


def _back(project_id: int) -> Response:
    return redirect(url_for("planning.project_plan", project_id=project_id))


@bp.get("/proyecto/<int:project_id>")
@roles_required(*VIEWERS)
def project_plan(project_id: int) -> str:
    session = db.session
    try:
        project = projects.get_project(session, project_id)
    except projects.ProjectError:
        abort(404)
    version = services.current_version(session, project_id)
    rows = services.allocations(session, version.id) if version else []
    span = services.plan_span(session, version.id) if version else None
    resources = list(
        session.scalars(select(Resource).where(Resource.is_active).order_by(Resource.display_name))
    )
    return render_template(
        "planning/plan.html",
        project=project,
        version=version,
        versions=services.versions(session, project_id),
        rows=rows,
        total=sum((r.hours for r in rows), Decimal(0)),
        span=span,
        warning=projects.span_warning(session, span, _today()),
        resources=resources,
        resource_names={r.id: r.display_name for r in resources},
        phases={p.id: p.name for p in project.phases},
        applications=[a for a in catalogs.list_applications(session) if a.is_active],
        application_names={a.id: a.name for a in catalogs.list_applications(session)},
        today=_today(),
        can_edit=current_user.has_role(*PLANNERS),
    )


@bp.post("/proyecto/<int:project_id>/asignaciones")
@roles_required(*PLANNERS)
def add_allocation(project_id: int) -> Response:
    form = request.form
    try:
        data = services.AllocationData(
            resource_id=_int(form.get("persona")) or 0,
            project_phase_id=_int(form.get("fase")),
            application_id=_int(form.get("aplicacion")),
            start_on=form.get("desde", ""),  # type: ignore[arg-type]
            end_on=form.get("hasta", ""),  # type: ignore[arg-type]
            hours=_hours(form.get("horas")),
        )
        services.add_allocation(db.session, project_id, data, actor_id=int(current_user.id))
    except (ValueError, ValidationError) as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash("Asignación agregada al plan.", "info")
    return _back(project_id)


@bp.post("/proyecto/<int:project_id>/asignaciones/<int:allocation_id>/quitar")
@roles_required(*PLANNERS)
def remove_allocation(project_id: int, allocation_id: int) -> Response:
    try:
        services.remove_allocation(
            db.session, project_id, allocation_id, actor_id=int(current_user.id)
        )
    except services.PlanError as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash("Asignación quitada.", "info")
    return _back(project_id)


@bp.post("/proyecto/<int:project_id>/linea-base")
@roles_required(*PLANNERS)
def set_baseline(project_id: int) -> Response:
    try:
        services.set_baseline(db.session, project_id, actor_id=int(current_user.id))
    except services.PlanError as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash("Línea base fijada. Los cambios futuros irán en replanificaciones.", "info")
    return _back(project_id)


@bp.post("/proyecto/<int:project_id>/replanificar")
@roles_required(*PLANNERS)
def replan(project_id: int) -> Response:
    try:
        services.replan(
            db.session,
            project_id,
            reason=request.form.get("motivo"),
            actor_id=int(current_user.id),
        )
    except (MissingReasonError, services.PlanError) as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash("Replanificación creada: ya puedes ajustar el plan.", "info")
    return _back(project_id)


@bp.get("/capacidad")
@roles_required(*CAPACITY_VIEWERS)
def capacity() -> str:
    try:
        start = date.fromisoformat(request.args.get("desde", ""))
    except ValueError:
        start = _today()
    count = min(max(_int(request.args.get("semanas")) or 8, 1), MAX_WEEKS)
    periods = services.weeks(start, count)
    first, last = periods[0][0], periods[-1][1]
    return render_template(
        "planning/capacity.html",
        loads=services.capacity(db.session, first, last),
        periods=periods,
        week_days=[[a + timedelta(days=n) for n in range(7)] for a, _ in periods],
        start=first,
        count=count,
        prev_start=first - timedelta(weeks=count),
        next_start=first + timedelta(weeks=count),
    )
