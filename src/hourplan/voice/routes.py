"""Dictation screen: record, wait for the draft, correct it and confirm. Each person sees theirs."""

from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user
from sqlalchemy import select
from werkzeug.wrappers import Response

from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.extensions import db, limiter
from hourplan.resources.models import Resource
from hourplan.shared.i18n import _
from hourplan.voice import services
from hourplan.voice.models import DictationStatus, VoiceDictation
from hourplan.voice.setup import enabled

bp = Blueprint("voice", __name__, url_prefix="/voz", template_folder="templates")

WRITERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER)
IN_PROGRESS = (DictationStatus.QUEUED.value, DictationStatus.PROCESSING.value)


@bp.before_request
def _only_when_enabled() -> None:
    if not enabled(current_app.config):
        abort(404)


def _resource() -> Resource:
    resource = db.session.scalars(
        select(Resource).where(Resource.user_id == int(current_user.id), Resource.is_active)
    ).first()
    if resource is None:
        abort(404)
    return resource


def _upload_limit() -> str:
    return str(current_app.config["VOICE_UPLOAD_LIMIT"])


def _user_key() -> str:
    return f"voice:{current_user.get_id()}"


@bp.get("")
@roles_required(*WRITERS)
def index() -> str:
    resource = _resource()
    recent = db.session.scalars(
        select(VoiceDictation)
        .where(
            VoiceDictation.resource_id == resource.id,
            VoiceDictation.status.in_(
                [*IN_PROGRESS, DictationStatus.READY.value, DictationStatus.FAILED.value]
            ),
        )
        .order_by(VoiceDictation.created_at.desc())
        .limit(10)
    ).all()
    return render_template(
        "voice/index.html",
        recent=recent,
        max_seconds=int(current_app.config["VOICE_MAX_SECONDS"]),
    )


@bp.post("/dictados")
@roles_required(*WRITERS)
@limiter.limit(_upload_limit, key_func=_user_key)
def upload() -> tuple[Response, int]:
    resource = _resource()
    try:
        item = services.create_dictation(
            db.session,
            resource.id,
            request.get_data(),
            actor_id=int(current_user.id),
            max_seconds=float(current_app.config["VOICE_MAX_SECONDS"]),
        )
    except services.VoiceError as err:
        return jsonify(error=str(err)), 400
    return jsonify(url=url_for("voice.show", dictation_id=item.id)), 201


def _own(dictation_id: int) -> VoiceDictation:
    try:
        return services.get_for(db.session, dictation_id, _resource().id)
    except services.VoiceError:
        abort(404)


def _line_view(line: dict[str, Any]) -> dict[str, Any]:
    return {"use": True, **line}


def _render_status(item: VoiceDictation, lines: list[dict[str, Any]] | None = None) -> str:
    cands = services.candidates(db.session) if item.status == DictationStatus.READY.value else None
    phase_names = {p.id: p.label for p in cands.phases} if cands else {}
    return render_template(
        "voice/_status.html",
        item=item,
        lines=lines if lines is not None else [_line_view(x) for x in item.draft or []],
        cands=cands,
        phase_names=phase_names,
        in_progress=item.status in IN_PROGRESS,
    )


@bp.get("/dictados/<int:dictation_id>")
@roles_required(*WRITERS)
def show(dictation_id: int) -> str:
    item = _own(dictation_id)
    return render_template("voice/show.html", item=item, status=_render_status(item))


@bp.get("/dictados/<int:dictation_id>/estado")
@roles_required(*WRITERS)
def status(dictation_id: int) -> str:
    return _render_status(_own(dictation_id))


def _int(raw: str) -> int | None:
    return int(raw) if raw.isdigit() else None


def _lines_from_form() -> list[dict[str, Any]]:
    form = request.form
    count = min(_int(form.get("n", "0")) or 0, services.MAX_LINES)
    lines: list[dict[str, Any]] = []
    for i in range(count):
        target = form.get(f"l{i}-destino", "")
        kind, _sep, raw = target.partition(":")
        lines.append(
            {
                "use": form.get(f"l{i}-usar") == "1",
                "work_date": form.get(f"l{i}-fecha", ""),
                "hours": form.get(f"l{i}-horas", ""),
                "project_id": _int(raw) if kind == "p" else None,
                "activity_bucket_id": _int(raw) if kind == "b" else None,
                "project_phase_id": _int(form.get(f"l{i}-fase", "")),
                "task_type_id": _int(form.get(f"l{i}-tipo", "")),
                "application_id": _int(form.get(f"l{i}-app", "")),
                "description": form.get(f"l{i}-desc", "").strip() or None,
                "ai_assisted": form.get(f"l{i}-ia") == "1",
                "notes": [],
                "origins": {},
            }
        )
    return lines


@bp.post("/dictados/<int:dictation_id>/confirmar")
@roles_required(*WRITERS)
def confirm(dictation_id: int) -> Response | tuple[str, int]:
    item = _own(dictation_id)
    lines = _lines_from_form()
    chosen = [line for line in lines if line["use"]]
    try:
        saved = services.confirm(
            db.session, item.id, item.resource_id, chosen, actor_id=int(current_user.id)
        )
    except services.VoiceError as err:
        db.session.rollback()
        flash(str(err), "error")
        item = _own(dictation_id)
        if item.status != DictationStatus.READY.value:
            return redirect(url_for("voice.show", dictation_id=item.id))
        return render_template(
            "voice/show.html", item=item, status=_render_status(item, lines)
        ), 400
    flash((_("Se guardaron %s entradas de horas.") % (saved,)), "success")
    return redirect(url_for("timesheet.week"))


@bp.post("/dictados/<int:dictation_id>/descartar")
@roles_required(*WRITERS)
def discard(dictation_id: int) -> Response:
    item = _own(dictation_id)
    services.discard(db.session, item.id, item.resource_id)
    flash("Dictado descartado.", "success")
    return redirect(url_for("voice.index"))
