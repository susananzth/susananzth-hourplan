"""Time bank screens: balance, claims, and the leader's decisions."""

from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import select
from werkzeug.wrappers import Response

from hourplan.audit.services import MissingReasonError
from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.extensions import db
from hourplan.resources.models import Resource
from hourplan.shared.i18n import N_, _, translated
from hourplan.timesheet import bank
from hourplan.timesheet.models import BankReason, OvertimeKind

bp = Blueprint("bank", __name__, url_prefix="/banco", template_folder="templates")

MEMBERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER)
DECIDERS = (Role.ADMIN, Role.PM, Role.LEAD)
REASONS = {
    BankReason.OVERTIME.value: N_("Horas extra"),
    BankReason.CONTINGENCY.value: N_("Contingencia no planificada"),
    BankReason.COMPENSATION.value: N_("Día compensado"),
    BankReason.ADJUSTMENT.value: N_("Ajuste"),
}
KINDS = {
    OvertimeKind.VOLUNTARY.value: N_("Voluntarias"),
    OvertimeKind.REQUIRED.value: N_("Exigidas"),
}
STATUSES = {"pending": N_("Pendiente"), "approved": N_("Aprobado"), "rejected": N_("Rechazado")}


def _today() -> date:
    return datetime.now().astimezone().date()


def _resource() -> Resource | None:
    return db.session.scalars(
        select(Resource).where(Resource.user_id == int(current_user.id), Resource.is_active)
    ).first()


def _error(err: Exception) -> str:
    return str(err)


@bp.get("")
@roles_required(*MEMBERS)
def index() -> str:
    resource = _resource()
    if resource is None:
        return render_template("timesheet/no_resource.html")
    return render_template(
        "timesheet/bank.html",
        balance=bank.balance(db.session, resource.id),
        waiting=bank.pending_hours(db.session, resource.id),
        moves=bank.movements(db.session, resource.id),
        pending=bank.pending_movements(db.session) if current_user.has_role(*DECIDERS) else [],
        reasons=translated(REASONS),
        kinds=translated(KINDS),
        statuses=translated(STATUSES),
        today=_today(),
    )


def _day() -> date:
    try:
        return date.fromisoformat(request.form.get("dia", ""))
    except ValueError:
        abort(400)


@bp.post("/reclamar")
@roles_required(*MEMBERS)
def claim() -> Response:
    resource = _resource()
    if resource is None:
        abort(404)
    day = _day()
    actor = int(current_user.id)
    try:
        if request.form.get("motivo") == BankReason.CONTINGENCY.value:
            bank.claim_contingency(db.session, resource.id, day, actor_id=actor)
        else:
            try:
                kind = OvertimeKind(request.form.get("tipo", ""))
            except ValueError:
                abort(400)
            bank.claim_overtime(db.session, resource.id, day, kind, actor_id=actor)
    except bank.BankError as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash("Solicitud enviada para aprobación.", "info")
    return redirect(url_for("bank.index"))


@bp.post("/<int:movement_id>/decidir")
@roles_required(*DECIDERS)
def decide(movement_id: int) -> Response:
    approve = request.form.get("decision") == "approve"
    try:
        bank.decide(
            db.session,
            movement_id,
            approve=approve,
            decider_id=int(current_user.id),
            reason=request.form.get("motivo_rechazo"),
        )
    except MissingReasonError:
        db.session.rollback()
        flash("Para rechazar escribe un motivo.", "error")
    except bank.BankError as err:
        db.session.rollback()
        flash(_error(err), "error")
    else:
        flash(_("Movimiento aprobado.") if approve else _("Movimiento rechazado."), "info")
    return redirect(url_for("bank.index"))
