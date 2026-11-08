"""Stopwatch, interruption, inactivity and alert settings. Each person uses only their own."""

from datetime import datetime
from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from pydantic import ValidationError
from sqlalchemy import select
from werkzeug.wrappers import Response

from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.extensions import db
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.shared.labels import validation_message
from hourplan.timer import alerts, services
from hourplan.timer.models import AlertSetting, TimerSession
from hourplan.timesheet import services as timesheet
from hourplan.timesheet.models import TimeEntry
from hourplan.timesheet.routes import decode_key, encode_key
from hourplan.timesheet.services import RowKey

bp = Blueprint("timer", __name__, url_prefix="/cronometro", template_folder="templates")

WRITERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER)


def _resource() -> Resource | None:
    return db.session.scalars(
        select(Resource).where(Resource.user_id == int(current_user.id), Resource.is_active)
    ).first()


def _need_resource() -> Resource:
    resource = _resource()
    if resource is None:
        abort(404)
    return resource


def _now() -> datetime:
    return services.utcnow()


def _message(err: Exception) -> str:
    return validation_message(err) if isinstance(err, ValidationError) else str(err)


def _back() -> Response:
    return redirect(url_for("timer.index"))


def _label(session: Any, timer: TimerSession) -> str:
    if timer.is_interruption and timer.project_id is None and timer.activity_bucket_id is None:
        return "Interrupción sin clasificar"
    return timesheet.target_label(
        session,
        RowKey(
            project_id=timer.project_id,
            activity_bucket_id=timer.activity_bucket_id,
            project_phase_id=timer.project_phase_id,
            task_type_id=timer.task_type_id,
        ),
    )


def _task_types() -> list[CatalogItem]:
    return list(
        db.session.scalars(
            select(CatalogItem)
            .where(CatalogItem.kind == CatalogKind.TASK_TYPE.value, CatalogItem.is_active)
            .order_by(CatalogItem.name)
        )
    )


def _bar(resource: Resource, *, error: str | None = None, notice: str | None = None) -> str:
    now = _now()
    services.settle(db.session, resource.id, now)
    current = services.heartbeat(db.session, resource.id, now) if request.method == "POST" else None
    open_ = services.open_timers(db.session, resource.id, now)
    found = alerts.pending(db.session, resource.id, now) if request.method == "POST" else []
    running = current or next((t for t in open_ if t.status == "running"), None)
    # Without a running timer the bar offers the last paused one, so it can be resumed or ended.
    shown = running or (open_[-1] if open_ else None)
    elapsed = services.elapsed_seconds(shown, now) if shown else 0
    return render_template(
        "timer/_bar.html",
        timer=shown,
        is_running=running is not None,
        unclassified=bool(shown and shown.project_id is None and shown.activity_bucket_id is None),
        paused=[t for t in open_ if t is not shown],
        label=_label(db.session, shown) if shown else "",
        elapsed=elapsed,
        total=(shown.chain_seconds + elapsed) if shown else 0,
        favorites=[] if shown else timesheet.favorites(db.session, resource.id)[:5],
        task_types=_task_types() if shown else [],
        encode_key=encode_key,
        error=error,
        notice=notice,
        idle_seconds=services.idle_minutes(db.session, resource.id) * 60,
        alerts=[
            {
                "title": f"{a.code} ha consumido {a.consumed_pct} % de sus horas",
                "body": f"{a.title}: superó el umbral de {a.percent} %.",
            }
            for a in found
        ],
    )


def _reply(resource: Resource, *, error: str | None = None, notice: str | None = None) -> Any:
    """The bar answers its own HTMX requests in place; the page gets a redirect and a flash."""
    if request.headers.get("HX-Request") == "true":
        return _bar(resource, error=error, notice=notice)
    if error:
        flash(error, "error")
    elif notice:
        flash(notice)
    return _back()


@bp.route("/latido", methods=["GET", "POST"])
@roles_required(*WRITERS)
def beat() -> str:
    """The bar asks for this every minute: it is the heartbeat and the alert check."""
    resource = _resource()
    if resource is None:
        return '<div id="timer-bar" hidden></div>'
    return _bar(resource)


@bp.get("")
@roles_required(*WRITERS)
def index() -> str:
    resource = _resource()
    if resource is None:
        return render_template("timesheet/no_resource.html")
    now = _now()
    services.settle(db.session, resource.id, now)
    timers = services.open_timers(db.session, resource.id, now)
    return render_template(
        "timer/index.html",
        timers=[(t, _label(db.session, t), services.elapsed_seconds(t, now)) for t in timers],
        idle_minutes=services.idle_minutes(db.session, resource.id),
        task_types=_task_types(),
        encode_key=encode_key,
        choices=services.IDLE_CHOICES,
    )


class _FailedError(Exception):
    def __init__(self, reply: Any) -> None:
        self.reply = reply


def _run(resource: Resource, action: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a service call; on a user error raise ``_FailedError`` carrying the reply to send."""
    try:
        return action(db.session, *args, **kwargs)
    except (services.TimerError, timesheet.EntryError, ValidationError) as err:
        db.session.rollback()
        raise _FailedError(_reply(resource, error=_message(err))) from err


@bp.errorhandler(_FailedError)
def _failed(err: _FailedError) -> Any:
    return err.reply


def _saved(entry: TimeEntry | None) -> str | None:
    if entry is None:
        return None
    hours = f"{entry.hours:.2f}".replace(".", ",")
    key = RowKey(project_id=entry.project_id, activity_bucket_id=entry.activity_bucket_id)
    return f"Guardado: {timesheet.target_label(db.session, key)} · {hours} h"


def _picked() -> RowKey | None:
    raw = request.form.get("nueva", "")
    return decode_key(raw) if raw else None


@bp.post("/iniciar")
@roles_required(*WRITERS)
def start() -> Any:
    resource = _need_resource()
    key = _picked()
    if key is None:
        return _reply(resource, error="Elige un proyecto o una actividad para iniciar.")
    _run(
        resource,
        services.start,
        resource.id,
        key,
        _now(),
        description=request.form.get("descripcion"),
    )
    return _reply(resource)


@bp.post("/cambiar")
@roles_required(*WRITERS)
def switch() -> Any:
    """Save the running task and continue on another one; the bar's clock keeps going."""
    resource = _need_resource()
    key = _picked()
    if key is None:
        return _reply(resource, error="Elige la nueva tarea.")
    entry, _ = _run(
        resource, services.switch, resource.id, key, _now(), actor_id=int(current_user.id)
    )
    return _reply(resource, notice=_saved(entry))


@bp.post("/pausar/<int:timer_id>")
@roles_required(*WRITERS)
def pause(timer_id: int) -> Any:
    resource = _need_resource()
    _run(resource, services.pause, resource.id, timer_id, _now())
    return _reply(resource)


@bp.post("/reanudar/<int:timer_id>")
@roles_required(*WRITERS)
def resume(timer_id: int) -> Any:
    resource = _need_resource()
    _run(resource, services.resume, resource.id, timer_id, _now())
    return _reply(resource)


@bp.post("/interrupcion")
@roles_required(*WRITERS)
def interrupt() -> Any:
    resource = _need_resource()
    services.interrupt(db.session, resource.id, _now())
    return _reply(resource)


@bp.post("/detener/<int:timer_id>")
@roles_required(*WRITERS)
def stop(timer_id: int) -> Any:
    resource = _need_resource()
    key = _picked()
    task_type = request.form.get("tipo", "")
    if task_type.isdigit():
        base = key
        if base is None:
            timer = db.session.get(TimerSession, timer_id)
            base = (
                RowKey(timer.project_id, timer.activity_bucket_id, timer.project_phase_id)
                if timer is not None and timer.resource_id == resource.id
                else None
            )
        if base is not None:
            key = RowKey(
                base.project_id, base.activity_bucket_id, base.project_phase_id, int(task_type)
            )
    entry = _run(
        resource,
        services.stop,
        resource.id,
        timer_id,
        _now(),
        key=key,
        description=request.form.get("descripcion"),
        ai_assisted=request.form.get("ia") == "1",
        actor_id=int(current_user.id),
    )
    return _reply(resource, notice=_saved(entry))


@bp.post("/descartar/<int:timer_id>")
@roles_required(*WRITERS)
def discard(timer_id: int) -> Any:
    resource = _need_resource()
    _run(resource, services.discard, resource.id, timer_id, _now())
    return _reply(resource, notice="Cronómetro descartado.")


@bp.post("/inactividad")
@roles_required(*WRITERS)
def idle() -> Response:
    resource = _need_resource()
    try:
        seconds = int(request.form.get("segundos", "0"))
        services.apply_idle(
            db.session, resource.id, seconds, request.form.get("opcion", ""), _now()
        )
    except ValueError, services.TimerError:
        db.session.rollback()
        return Response(status=400)
    return Response(status=204)


@bp.post("/preferencias")
@roles_required(*WRITERS)
def preferences() -> Response:
    resource = _need_resource()
    try:
        minutes = int(request.form.get("minutos", ""))
        services.set_idle_minutes(db.session, resource.id, minutes)
        flash("Umbral de inactividad guardado.")
    except ValueError as err:
        db.session.rollback()
        flash(
            str(err)
            if isinstance(err, services.TimerError)
            else "Inactividad (minutos): escribe un número entre 1 y 120.",
            "error",
        )
    return _back()


# ----------------------------------------------------------------------------- alerts


@bp.get("/alertas")
@roles_required(*WRITERS)
def alert_settings() -> str:
    resource = _need_resource()
    projects = db.session.scalars(
        select(Project)
        .where(Project.closed_on.is_(None))
        .where(
            Project.id.in_(
                select(TimeEntry.project_id).where(
                    TimeEntry.resource_id == resource.id,
                    TimeEntry.project_id.is_not(None),
                )
            )
            | Project.id.in_(select(AlertSetting.project_id))
            | (Project.manager_id == int(current_user.id))
            | (Project.lead_id == int(current_user.id))
        )
        .order_by(Project.code)
    ).all()
    rows = [
        {
            "project": p,
            "template": alerts.template(db.session, p.id),
            "personal": alerts.personal(db.session, p.id, resource.id),
            "budget": alerts.budget(p),
            "can_edit": alerts.can_edit_template(
                p, int(current_user.id), is_admin=current_user.has_role("admin")
            ),
        }
        for p in projects
    ]
    return render_template("timer/alerts.html", rows=rows)


def _thresholds(prefix: str, project_id: int) -> list[int]:
    return alerts.parse_thresholds(request.form.get(f"{prefix}{project_id}", ""))


@bp.post("/alertas/<int:project_id>/plantilla")
@roles_required(*WRITERS)
def save_template(project_id: int) -> Response:
    project = db.session.get(Project, project_id)
    if project is None:
        abort(404)
    if not alerts.can_edit_template(
        project, int(current_user.id), is_admin=current_user.has_role("admin")
    ):
        abort(403)
    try:
        alerts.set_thresholds(db.session, project_id, None, _thresholds("p", project_id))
        flash("Plantilla de alertas guardada.")
    except alerts.AlertError as err:
        db.session.rollback()
        flash(str(err), "error")
    return redirect(url_for("timer.alert_settings"))


@bp.post("/alertas/<int:project_id>/mio")
@roles_required(*WRITERS)
def save_personal(project_id: int) -> Response:
    resource = _need_resource()
    try:
        if request.form.get("accion") == "plantilla":
            alerts.set_thresholds(db.session, project_id, resource.id, None)
            flash("Vuelves a usar los umbrales de la plantilla.")
        else:
            alerts.set_thresholds(db.session, project_id, resource.id, _thresholds("m", project_id))
            flash("Tus umbrales se guardaron.")
    except alerts.AlertError as err:
        db.session.rollback()
        flash(str(err), "error")
    return redirect(url_for("timer.alert_settings"))


@bp.app_context_processor
def _globals() -> dict[str, object]:
    return {"idle_choices": services.IDLE_CHOICES}
