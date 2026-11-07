"""Weekly time grid. Each person edits only their own hours here."""

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, render_template, request
from flask_login import current_user
from pydantic import ValidationError
from sqlalchemy import select

from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.extensions import db
from hourplan.resources.models import Resource
from hourplan.timesheet import services
from hourplan.timesheet.services import RowKey

bp = Blueprint("timesheet", __name__, url_prefix="/horas", template_folder="templates")

WRITERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER)


def encode_key(key: RowKey) -> str:
    parts = (key.project_id, key.activity_bucket_id, key.project_phase_id, key.task_type_id)
    return ",".join("" if p is None else str(p) for p in parts)


def decode_key(raw: str) -> RowKey:
    parts = raw.split(",")
    if len(parts) != 4:
        abort(400)
    try:
        values = [int(p) if p else None for p in parts]
    except ValueError:
        abort(400)
    return RowKey(*values)


def _today() -> date:
    return datetime.now().astimezone().date()


def _resource() -> Resource | None:
    return db.session.scalars(
        select(Resource).where(Resource.user_id == int(current_user.id), Resource.is_active)
    ).first()


def _day(raw: str | None, default: date) -> date:
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        abort(400)


def _hours(raw: str) -> Decimal:
    raw = raw.strip().replace(",", ".")
    if not raw:
        return Decimal(0)
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValueError("Escribe las horas como número, por ejemplo 1,5.") from None


def _int(raw: str | None) -> int:
    try:
        return int(raw or "")
    except ValueError:
        abort(400)


def _extras() -> list[RowKey]:
    return [
        decode_key(raw)
        for raw in request.values.getlist("extra") + request.values.getlist("nueva")
        if raw
    ]


def _render(resource: Resource, error: str | None = None, notice: str | None = None) -> str:
    start = services.week_start(_day(request.values.get("semana"), _today()))
    extras = _extras()
    grid = services.week_grid(db.session, resource, start, extra_rows=extras)
    template = (
        "timesheet/_grid.html" if request.headers.get("HX-Request") else "timesheet/week.html"
    )
    return render_template(
        template,
        grid=grid,
        start=start,
        previous=start - timedelta(days=7),
        following=start + timedelta(days=7),
        today=_today(),
        extras=[encode_key(k) for k in extras],
        encode_key=encode_key,
        favorites=services.favorites(db.session, resource.id),
        favorite_keys={encode_key(k) for k in services.favorite_keys(db.session, resource.id)},
        templates=services.list_templates(db.session, resource.id),
        error=error,
        notice=notice,
    )


@bp.get("")
@roles_required(*WRITERS)
def week() -> str:
    resource = _resource()
    if resource is None:
        return render_template("timesheet/no_resource.html")
    return _render(resource)


@bp.post("/celda")
@roles_required(*WRITERS)
def cell() -> str:
    resource = _resource()
    if resource is None:
        abort(404)
    day = _day(request.form.get("dia"), _today())
    key = decode_key(request.form.get("fila", ""))
    try:
        summary = services.set_cell(
            db.session,
            resource.id,
            key,
            day,
            _hours(request.form.get("horas", "")),
            actor_id=int(current_user.id),
        )
    except (ValueError, ValidationError) as err:
        db.session.rollback()
        return _render(resource, error=_cell_error(err))
    notice = None
    if summary.over_warning:
        notice = f"El {day:%d/%m} suma {summary.worked} h: pasa de {summary.warning_at} h."
    return _render(resource, notice=notice)


@bp.post("/repetir")
@roles_required(*WRITERS)
def repeat() -> str:
    resource = _resource()
    if resource is None:
        abort(404)
    day = _day(request.form.get("dia"), _today())
    try:
        services.repeat_day(db.session, resource.id, day, actor_id=int(current_user.id))
    except services.EntryError as err:
        return _render(resource, error=_cell_error(err))
    return _render(resource, notice="Se copió el último día con horas.")


@bp.post("/favorito")
@roles_required(*WRITERS)
def favorite() -> str:
    resource = _resource()
    if resource is None:
        abort(404)
    try:
        services.toggle_favorite(db.session, resource.id, decode_key(request.form.get("fila", "")))
    except services.EntryError as err:
        return _render(resource, error=_cell_error(err))
    return _render(resource)


@bp.post("/plantilla/guardar")
@roles_required(*WRITERS)
def template_save() -> str:
    resource = _resource()
    if resource is None:
        abort(404)
    try:
        services.save_day_as_template(
            db.session,
            resource.id,
            request.form.get("nombre", ""),
            _day(request.form.get("dia"), _today()),
        )
    except services.EntryError as err:
        return _render(resource, error=_cell_error(err))
    return _render(resource, notice="Plantilla guardada.")


@bp.post("/plantilla/aplicar")
@roles_required(*WRITERS)
def template_apply() -> str:
    resource = _resource()
    if resource is None:
        abort(404)
    try:
        services.apply_template(
            db.session,
            resource.id,
            _int(request.form.get("plantilla")),
            _day(request.form.get("dia"), _today()),
            actor_id=int(current_user.id),
        )
    except services.EntryError as err:
        return _render(resource, error=_cell_error(err))
    return _render(resource, notice="Plantilla aplicada.")


@bp.post("/plantilla/borrar")
@roles_required(*WRITERS)
def template_delete() -> str:
    resource = _resource()
    if resource is None:
        abort(404)
    try:
        services.delete_template(db.session, resource.id, _int(request.form.get("plantilla")))
    except services.EntryError as err:
        return _render(resource, error=_cell_error(err))
    return _render(resource, notice="Plantilla borrada.")


@bp.get("/buscar")
@roles_required(*WRITERS)
def search() -> str:
    found = services.search_targets(db.session, request.args.get("q", ""))
    return render_template("timesheet/_results.html", found=found, encode_key=encode_key)


@bp.post("/fila")
@roles_required(*WRITERS)
def add_row() -> str:
    resource = _resource()
    if resource is None:
        abort(404)
    return _render(resource)


def _cell_error(err: Exception) -> str:
    if isinstance(err, ValidationError):
        return "Las horas deben ser múltiplos de 0,25 y no más de 24."
    if isinstance(err, services.EntryError):
        return {
            "The day already has hours": "Ese día ya tiene horas.",
            "There is no earlier day to repeat": "No hay un día anterior para repetir.",
            "The source day has no entries": "El día anterior no tiene horas.",
            "A template needs a name": "Escribe un nombre para la plantilla.",
            "The day has no entries to save": "Ese día no tiene horas para guardar.",
            "Unknown template": "No se encontró la plantilla.",
        }.get(str(err), "No se pudo guardar: la celda tiene varias entradas.")
    return str(err)
