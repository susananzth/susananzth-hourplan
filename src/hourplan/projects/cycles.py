"""Validation rounds of verification and approval phases (gates).

Each round is submitted and later approved or returned ("observado"). A returned round sends
the work back to construction: it needs observations and a process-origin category, never a
person to blame. Rework is later derived from returned rounds.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.catalogs import services as catalogs
from hourplan.catalogs.models import CatalogKind
from hourplan.db import transaction
from hourplan.projects.models import CycleOutcome, PhaseCycle, PhaseStatus, PhaseType, ProjectPhase
from hourplan.shared.labels import label
from hourplan.shared.translate import _

ENTITY = "phase_cycle"
GATED_TYPES = frozenset({PhaseType.VERIFICATION.value, PhaseType.APPROVAL.value})


class CycleError(ValueError):
    pass


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    outcome: CycleOutcome
    resolved_on: date
    observations: str | None = Field(default=None, max_length=5000)
    rework_category_id: int | None = None
    rework_reason_id: int | None = None

    @field_validator("outcome")
    @classmethod
    def _decided(cls, value: CycleOutcome) -> CycleOutcome:
        if value == CycleOutcome.PENDING:
            raise ValueError("Elige aprobar u observar la ronda.")
        return value

    @field_validator("observations")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None


def is_gated(phase: ProjectPhase) -> bool:
    """Phases that go through rounds: verification, approval, or flagged as needing approval."""
    return phase.phase_type in GATED_TYPES or phase.requires_approval


def round_name(phase: ProjectPhase, cycle: PhaseCycle) -> str:
    prefix = "V" if phase.phase_type == PhaseType.VERIFICATION.value else "A"
    return f"{prefix}-{cycle.number}"


def _phase(session: Session, phase_id: int) -> ProjectPhase:
    phase = session.get(ProjectPhase, phase_id)
    if phase is None:
        raise CycleError("No se encontró esa fase.")
    return phase


def submit(
    session: Session, phase_id: int, submitted_on: date, *, actor_id: int | None
) -> PhaseCycle:
    """Open the next round of a gated phase. Only one round can be pending at a time."""
    phase = _phase(session, phase_id)
    if not is_gated(phase):
        raise CycleError("Esta fase no tiene rondas de verificación ni de aprobación.")
    last = session.scalars(
        select(PhaseCycle)
        .where(PhaseCycle.project_phase_id == phase_id)
        .order_by(PhaseCycle.number.desc())
        .limit(1)
    ).first()
    if last is not None:
        if last.outcome == CycleOutcome.PENDING.value:
            raise CycleError("Ya hay una ronda pendiente: resuélvela antes de enviar otra.")
        if last.outcome == CycleOutcome.APPROVED.value:
            raise CycleError("Esta fase ya fue aprobada.")
        if last.resolved_on and submitted_on < last.resolved_on:
            raise CycleError("El envío no puede ser anterior a la devolución de la ronda previa.")
    with transaction(session):
        cycle = PhaseCycle(
            project_phase_id=phase_id,
            number=(last.number + 1) if last else 1,
            submitted_on=submitted_on,
        )
        session.add(cycle)
        if phase.status == PhaseStatus.PENDING.value:
            phase.status = PhaseStatus.IN_PROGRESS.value
        session.flush()
        audit.record(
            session,
            action=AuditAction.CREATE,
            entity_type=ENTITY,
            entity_id=cycle.id,
            actor_id=actor_id,
            changes={
                "project_phase_id": phase_id,
                "number": cycle.number,
                "submitted_on": submitted_on.isoformat(),
            },
        )
    return cycle


def _check_origin(session: Session, data: Decision) -> None:
    if data.outcome != CycleOutcome.RETURNED:
        return
    if not data.observations:
        raise CycleError("Para observar una ronda escribe las observaciones.")
    if data.rework_category_id is None:
        raise CycleError(
            _("Para observar una ronda elige la %s.") % (label("rework_category_id").lower(),)
        )
    try:
        catalogs.require_item(session, data.rework_category_id, CatalogKind.REWORK_CATEGORY)
        if data.rework_reason_id is not None:
            reason = catalogs.require_item(
                session, data.rework_reason_id, CatalogKind.REWORK_REASON
            )
            if reason.parent_id != data.rework_category_id:
                raise CycleError(
                    _("%s: no pertenece a la categoría elegida.") % (label("rework_reason_id"),)
                )
    except catalogs.CatalogError:
        raise CycleError("Esa categoría o motivo de rework no existe o está inactivo.") from None


def decide(
    session: Session, cycle_id: int, data: Decision, *, decider_id: int | None
) -> PhaseCycle:
    """Approve or return a pending round. Approving closes the phase."""
    cycle = session.get(PhaseCycle, cycle_id)
    if cycle is None:
        raise CycleError("No se encontró esa ronda.")
    if cycle.outcome != CycleOutcome.PENDING.value:
        raise CycleError("Esa ronda ya fue resuelta.")
    if data.resolved_on < cycle.submitted_on:
        raise CycleError("La resolución no puede ser anterior al envío.")
    _check_origin(session, data)
    approved = data.outcome == CycleOutcome.APPROVED
    values = {
        "outcome": data.outcome.value,
        "resolved_on": data.resolved_on,
        "observations": data.observations,
        "rework_category_id": None if approved else data.rework_category_id,
        "rework_reason_id": None if approved else data.rework_reason_id,
        "decided_by_id": decider_id,
    }
    with transaction(session):
        before = {key: getattr(cycle, key) for key in values}
        for key, value in values.items():
            setattr(cycle, key, value)
        if approved:
            cycle.phase.status = PhaseStatus.DONE.value
        audit.record(
            session,
            action=AuditAction.UPDATE,
            entity_type=ENTITY,
            entity_id=cycle.id,
            actor_id=decider_id,
            changes=audit.diff(before, values),
        )
    return cycle


def returned_count(session: Session, project_id: int) -> int:
    """How many rounds of the project were returned (each one means rework)."""
    return (
        session.scalar(
            select(func.count(PhaseCycle.id))
            .join(ProjectPhase, ProjectPhase.id == PhaseCycle.project_phase_id)
            .where(
                ProjectPhase.project_id == project_id,
                PhaseCycle.outcome == CycleOutcome.RETURNED.value,
            )
        )
        or 0
    )
