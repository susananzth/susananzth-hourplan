"""Reports: plan versus actual per project, compliance, rework and the hours dashboard."""

from datetime import date, datetime, timedelta
from io import BytesIO

from flask import Blueprint, abort, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import select
from werkzeug.wrappers import Response

from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.catalogs import services as catalogs
from hourplan.extensions import db
from hourplan.policies import services as policies
from hourplan.policies.models import EvaluationPolicy
from hourplan.projects.models import Project
from hourplan.reports import compare, dashboard, notes
from hourplan.reports import export as xlsx
from hourplan.resources.models import Resource
from hourplan.shared.i18n import N_, translated

bp = Blueprint("reports", __name__, url_prefix="/informes", template_folder="templates")

MANAGERS = (Role.ADMIN, Role.PM, Role.LEAD)
PERSONAL = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER)
STATUS_LABELS = {
    "green": (N_("En meta"), "✓"),
    "amber": (N_("Atención"), "!"),
    "red": (N_("Fuera de meta"), "✕"),
}
DEFAULT_MONTHS = 3


def _today() -> date:
    return datetime.now().astimezone().date()


def _int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def _date(raw: str | None) -> date | None:
    try:
        return date.fromisoformat((raw or "").strip())
    except ValueError:
        return None


def _period() -> tuple[date, date]:
    today = _today()
    default_start = (today.replace(day=1) - timedelta(days=31 * (DEFAULT_MONTHS - 1))).replace(
        day=1
    )
    start = _date(request.args.get("desde")) or default_start
    end = _date(request.args.get("hasta")) or today
    return (start, end) if start <= end else (end, start)


def _resource() -> Resource | None:
    return db.session.scalars(
        select(Resource).where(Resource.user_id == int(current_user.id), Resource.is_active)
    ).first()


@bp.app_context_processor
def _helpers() -> dict[str, object]:
    return {"status_labels": translated(STATUS_LABELS)}


@bp.get("/")
@roles_required(*MANAGERS)
def index() -> str:
    start, end = _period()
    return render_template(
        "reports/dashboard.html",
        start=start,
        end=end,
        totals=dashboard.totals(db.session, start, end),
        tables=[
            (dim, name, dashboard.hours_by(db.session, dim, start, end))
            for dim, name in dashboard.DIMENSIONS.items()
        ],
        categories=compare.rework_by_category(db.session, start=start, end=end),
    )


@bp.get("/proyectos")
@roles_required(*MANAGERS)
def projects() -> str:
    portfolio_id = _int(request.args.get("cartera"))
    return render_template(
        "reports/projects.html",
        rows=compare.summaries(
            db.session,
            portfolio_id=portfolio_id,
            include_closed=request.args.get("cerrados", "1") == "1",
            today=_today(),
        ),
        portfolios=[p for p in catalogs.list_portfolios(db.session) if p.is_active],
        portfolio_id=portfolio_id,
        show_closed=request.args.get("cerrados", "1") == "1",
    )


@bp.get("/proyectos/<int:project_id>")
@roles_required(*MANAGERS)
def project(project_id: int) -> str:
    today = _today()
    row = db.session.get(Project, project_id)
    if row is None:
        abort(404)
    snapshot = compare.get_snapshot(db.session, project_id)
    if snapshot:
        policy = (
            db.session.get(EvaluationPolicy, snapshot.policy_id) if snapshot.policy_id else None
        )
        evaluation = compare.from_snapshot(snapshot, policy)
    else:
        policy = policies.policy_at(db.session, EvaluationPolicy, row.closed_on or today)
        evaluation = None
    report = compare.project_report(db.session, project_id, policy=policy)
    recalculated = None
    if request.args.get("recalcular") == "1" and (snapshot or row.closed_on):
        recalculated = compare.recalculate(db.session, project_id, today)
    return render_template(
        "reports/project.html",
        project=row,
        report=report,
        evaluation=evaluation or report.evaluation,
        snapshot=snapshot,
        recalculated=recalculated,
        notes=notes.project_notes(db.session, project_id),
        today=today,
    )


@bp.post("/proyectos/<int:project_id>/cerrar")
@roles_required(*MANAGERS)
def close(project_id: int) -> Response:
    closed_on = _date(request.form.get("cierre"))
    try:
        if closed_on is None:
            raise compare.ReportError("Fecha de cierre: escribe una fecha válida.")
        compare.close_project(db.session, project_id, closed_on, actor_id=int(current_user.id))
    except compare.ReportError as err:
        db.session.rollback()
        flash(str(err), "error")
    else:
        flash("Proyecto cerrado y evaluación guardada.", "info")
    return redirect(url_for("reports.project", project_id=project_id))


@bp.post("/proyectos/<int:project_id>/evaluacion")
@roles_required(*MANAGERS)
def save_evaluation(project_id: int) -> Response:
    try:
        compare.save_snapshot(db.session, project_id, actor_id=int(current_user.id))
    except compare.ReportError as err:
        db.session.rollback()
        flash(str(err), "error")
    else:
        flash("Evaluación guardada con la política vigente a la fecha de cierre.", "info")
    return redirect(url_for("reports.project", project_id=project_id))


@bp.post("/proyectos/<int:project_id>/notas")
@roles_required(*MANAGERS)
def add_note(project_id: int) -> Response:
    try:
        notes.add_note(
            db.session, project_id, request.form.get("nota", ""), actor_id=int(current_user.id)
        )
    except compare.ReportError as err:
        db.session.rollback()
        flash(str(err), "error")
    else:
        flash("Nota de desvío guardada.", "info")
    return redirect(url_for("reports.project", project_id=project_id))


@bp.get("/mio")
@roles_required(*PERSONAL)
def mine() -> str:
    resource = _resource()
    start, end = _period()
    if resource is None:
        return render_template("reports/mine.html", start=start, end=end, lines=None)
    return render_template(
        "reports/mine.html",
        start=start,
        end=end,
        lines=compare.personal_lines(db.session, resource.id),
        totals=dashboard.totals(db.session, start, end, resource_id=resource.id),
        months=dashboard.hours_by(db.session, "mes", start, end, resource_id=resource.id),
        tasks=dashboard.hours_by(db.session, "tipo", start, end, resource_id=resource.id),
        notes=notes.notes_for_resource(db.session, resource.id),
    )


def _download(content: bytes, name: str) -> Response:
    return send_file(
        BytesIO(content), mimetype=xlsx.XLSX_TYPE, as_attachment=True, download_name=name
    )


@bp.get("/exportar/panel.xlsx")
@roles_required(*MANAGERS)
def export_dashboard() -> Response:
    start, end = _period()
    return _download(xlsx.panel_workbook(db.session, start, end), f"panel-horas_{start}_{end}.xlsx")


@bp.get("/exportar/proyectos.xlsx")
@roles_required(*MANAGERS)
def export_projects() -> Response:
    rows = compare.summaries(
        db.session,
        portfolio_id=_int(request.args.get("cartera")),
        include_closed=request.args.get("cerrados", "1") == "1",
        today=_today(),
    )
    return _download(xlsx.projects_workbook(rows), f"plan-contra-real_{_today()}.xlsx")


@bp.get("/exportar/mio.xlsx")
@roles_required(*PERSONAL)
def export_mine() -> Response:
    resource = _resource()
    if resource is None:
        abort(404)
    start, end = _period()
    content = xlsx.personal_workbook(db.session, resource.id, resource.display_name, start, end)
    return _download(content, f"mi-informe_{start}_{end}.xlsx")
