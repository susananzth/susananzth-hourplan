"""Absence screens: everyone requests their own; leads, PMs and admins decide."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from pydantic import ValidationError
from sqlalchemy import select
from werkzeug.wrappers import Response

from hourplan.audit.services import MissingReasonError
from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.extensions import db
from hourplan.resources.models import Resource
from hourplan.shared.i18n import N_, _, translated
from hourplan.shared.labels import label, validation_message
from hourplan.timesheet import absences, bank
from hourplan.timesheet.models import AbsenceType

bp = Blueprint("absences", __name__, url_prefix="/ausencias", template_folder="templates")

REQUESTERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER)
DECIDERS = (Role.ADMIN, Role.PM, Role.LEAD)
TYPE_LABELS = {
    AbsenceType.VACATION: N_("Vacaciones"),
    AbsenceType.SICK_LEAVE: N_("Licencia médica"),
    AbsenceType.PERMISSION: N_("Permiso"),
    AbsenceType.COMPENSATION: N_("Día compensado con el banco de horas"),
    AbsenceType.OTHER: N_("Otra"),
}
STATUS_LABELS = {
    "pending": N_("Pendiente"),
    "approved": N_("Aprobada"),
    "rejected": N_("Rechazada"),
}


def _today() -> date:
    return datetime.now().astimezone().date()


def _resource() -> Resource | None:
    return db.session.scalars(
        select(Resource).where(Resource.user_id == int(current_user.id), Resource.is_active)
    ).first()


def _parse_hours(raw: str) -> Decimal | None:
    raw = raw.strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValueError(
            _("%s: escribe un número, por ejemplo 4,5.") % (label("hours_per_day"),)
        ) from None


def _error(err: Exception) -> str:
    if isinstance(err, ValidationError):
        return validation_message(err)
    return str(err)


@bp.get("")
@roles_required(*REQUESTERS)
def index() -> str:
    resource = _resource()
    if resource is None:
        return render_template("timesheet/no_resource.html")
    return render_template(
        "timesheet/absences.html",
        mine=absences.absences_of(db.session, resource.id, since=_today()),
        pending=absences.pending_requests(db.session) if current_user.has_role(*DECIDERS) else [],
        types=translated(TYPE_LABELS),
        statuses=translated(STATUS_LABELS),
        today=_today(),
    )


@bp.post("")
@roles_required(*REQUESTERS)
def create() -> Response:
    resource = _resource()
    if resource is None:
        abort(404)
    try:
        data = absences.AbsenceData(
            absence_type=request.form.get("tipo", ""),  # type: ignore[arg-type]
            start_on=request.form.get("desde", ""),  # type: ignore[arg-type]
            end_on=request.form.get("hasta", ""),  # type: ignore[arg-type]
            hours_per_day=_parse_hours(request.form.get("horas", "")),
            notes=request.form.get("notas"),
        )
        absences.request_absence(db.session, resource.id, data, actor_id=int(current_user.id))
    except (ValueError, ValidationError) as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash("Solicitud enviada.", "info")
    return redirect(url_for("absences.index"))


@bp.post("/<int:absence_id>/cancelar")
@roles_required(*REQUESTERS)
def cancel(absence_id: int) -> Response:
    resource = _resource()
    if resource is None:
        abort(404)
    try:
        absences.cancel_absence(
            db.session,
            absence_id,
            resource_id=resource.id,
            today=_today(),
            actor_id=int(current_user.id),
        )
    except absences.AbsenceError:
        flash("No se pudo cancelar esa ausencia.", "error")
    else:
        flash("Ausencia cancelada.", "info")
    return redirect(url_for("absences.index"))


@bp.post("/<int:absence_id>/decidir")
@roles_required(*DECIDERS)
def decide(absence_id: int) -> Response:
    approve = request.form.get("decision") == "approve"
    try:
        absences.decide_absence(
            db.session,
            absence_id,
            approve=approve,
            decider_id=int(current_user.id),
            reason=request.form.get("motivo"),
        )
    except MissingReasonError:
        db.session.rollback()
        flash("Para rechazar escribe un motivo.", "error")
    except (absences.AbsenceError, bank.BankError) as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash(_("Solicitud aprobada.") if approve else _("Solicitud rechazada."), "info")
    return redirect(url_for("absences.index"))
