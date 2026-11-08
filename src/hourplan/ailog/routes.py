"""Import the AI work log and the calendar: upload, review the draft, confirm."""

from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select

from hourplan.ailog import parsers, services
from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.extensions import db
from hourplan.resources.models import Resource
from hourplan.timesheet.models import EntrySource

bp = Blueprint("ailog", __name__, url_prefix="/bitacora", template_folder="templates")

WRITERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER)
SOURCES = {"bitacora": EntrySource.AI_LOG, "calendario": EntrySource.IMPORT}
MAX_BYTES = 1024 * 1024


def _resource() -> Resource:
    resource = db.session.scalars(
        select(Resource).where(Resource.user_id == int(current_user.id), Resource.is_active)
    ).first()
    if resource is None:
        abort(404)
    return resource


def _int(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


def _lines_from_form() -> list[dict[str, Any]]:
    form = request.form
    count = min(_int(form.get("n", "0")) or 0, services.MAX_LINES)
    lines: list[dict[str, Any]] = []
    for i in range(count):
        kind, _, raw = form.get(f"l{i}-destino", "").partition(":")
        lines.append(
            {
                "use": form.get(f"l{i}-usar") == "1",
                "work_date": form.get(f"l{i}-fecha", ""),
                "hours": form.get(f"l{i}-horas", ""),
                "project_id": _int(raw) if kind == "p" else None,
                "activity_bucket_id": _int(raw) if kind == "b" else None,
                "task_type_id": _int(form.get(f"l{i}-tipo", "")),
                "application_id": _int(form.get(f"l{i}-app", "")),
                "description": form.get(f"l{i}-desc", "").strip() or None,
                "ai_assisted": form.get(f"l{i}-ia") == "1",
                "notes": [],
            }
        )
    return lines


def _page(kind: str, lines: list[dict[str, Any]] | None = None, status: int = 200) -> Any:
    return render_template(
        "ailog/index.html",
        kind=kind,
        lines=lines,
        choices=services.choices(db.session) if lines else None,
    ), status


@bp.get("")
@roles_required(*WRITERS)
def index() -> Any:
    _resource()
    return _page("bitacora" if request.args.get("tipo") != "calendario" else "calendario")


@bp.post("/leer")
@roles_required(*WRITERS)
def read() -> Any:
    resource = _resource()
    kind = request.form.get("tipo", "bitacora")
    if kind not in SOURCES:
        abort(400)
    upload = request.files.get("archivo")
    if upload is None or not upload.filename:
        flash("Elige un archivo.", "error")
        return _page(kind, status=400)
    raw = upload.stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        flash("El archivo pesa más de 1 MB.", "error")
        return _page(kind, status=400)
    try:
        text = raw.decode("utf-8-sig")
        if kind == "calendario":
            lines = services.draft_from_calendar(db.session, resource.id, parsers.parse_ics(text))
        else:
            lines = services.draft_from_log(
                db.session, resource.id, parsers.parse_log(upload.filename, text)
            )
    except UnicodeDecodeError:
        flash("El archivo debe estar en UTF-8.", "error")
        return _page(kind, status=400)
    except parsers.ParseError as err:
        flash(str(err), "error")
        return _page(kind, status=400)
    return _page(kind, lines)


@bp.post("/confirmar")
@roles_required(*WRITERS)
def confirm() -> Any:
    resource = _resource()
    kind = request.form.get("tipo", "bitacora")
    if kind not in SOURCES:
        abort(400)
    lines = _lines_from_form()
    chosen = [line for line in lines if line["use"]]
    try:
        saved = services.confirm(
            db.session,
            resource.id,
            chosen,
            source=SOURCES[kind],
            actor_id=int(current_user.id),
        )
    except services.AilogError as err:
        db.session.rollback()
        flash(str(err), "error")
        return _page(kind, lines, 400)
    flash(f"Se guardaron {saved} entradas de horas.", "success")
    return redirect(url_for("timesheet.week"))
