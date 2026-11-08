"""Projects: create from a phase template, edit, and adjust phase dates and effort."""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.db import transaction
from hourplan.policies import services as policies
from hourplan.policies.models import WorkPolicy
from hourplan.projects.models import (
    PhaseStatus,
    PhaseTemplate,
    Project,
    ProjectKind,
    ProjectPhase,
)
from hourplan.shared.labels import label

ENTITY = "project"
PHASE_ENTITY = "project_phase"
_STEP_FIELDS = (
    "position",
    "name",
    "phase_type",
    "estimated_by_other_role",
    "requires_approval",
    "blocks_next",
    "counts_as_delivery",
)


class ProjectError(ValueError):
    pass


class DuplicateCodeError(ProjectError):
    """Another project already uses that code."""


def _quarter(value: Decimal | None, field: str) -> Decimal | None:
    if value is not None and value % Decimal("0.25") != 0:
        raise ValueError(f"{label(field)} debe ser múltiplo de 0,25.")
    return value


class ProjectData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    portfolio_id: int | None = None
    requirement_type_id: int | None = None
    priority_id: int | None = None
    status_id: int | None = None
    phase_template_id: int | None = None
    parent_id: int | None = None
    manager_id: int | None = None
    lead_id: int | None = None
    base_effort_hours: Decimal | None = Field(default=None, ge=0, le=100000)
    contingency_hours: Decimal | None = Field(default=None, ge=0, le=100000)
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("base_effort_hours", "contingency_hours")
    @classmethod
    def _hours(cls, value: Decimal | None, info: Any) -> Decimal | None:
        return _quarter(value, info.field_name)

    @field_validator("notes")
    @classmethod
    def _blank_is_none(cls, value: str | None) -> str | None:
        return value or None


class PhaseData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: PhaseStatus
    planned_start: date | None = None
    planned_end: date | None = None
    base_effort_hours: Decimal | None = Field(default=None, ge=0, le=100000)
    contingency_hours: Decimal | None = Field(default=None, ge=0, le=100000)

    @field_validator("base_effort_hours", "contingency_hours")
    @classmethod
    def _hours(cls, value: Decimal | None, info: Any) -> Decimal | None:
        return _quarter(value, info.field_name)

    @model_validator(mode="after")
    def _dates(self) -> PhaseData:
        if (self.planned_start is None) != (self.planned_end is None):
            raise ValueError("Indica el inicio y el fin previstos, o deja ambos vacíos.")
        if self.planned_start and self.planned_end and self.planned_end < self.planned_start:
            raise ValueError("El fin previsto no puede ser anterior al inicio previsto.")
        return self


def get_project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ProjectError("No se encontró ese proyecto.")
    return project


def list_projects(
    session: Session,
    *,
    query: str = "",
    portfolio_id: int | None = None,
    status_id: int | None = None,
    include_closed: bool = False,
    limit: int = 200,
) -> list[Project]:
    stmt = select(Project).where(Project.kind == ProjectKind.PROJECT.value)
    query = query.strip()
    if query:
        like = f"%{query.replace('%', '').replace('_', '')}%"
        stmt = stmt.where(or_(Project.code.ilike(like), Project.title.ilike(like)))
    if portfolio_id:
        stmt = stmt.where(Project.portfolio_id == portfolio_id)
    if status_id:
        stmt = stmt.where(Project.status_id == status_id)
    if not include_closed:
        stmt = stmt.where(Project.closed_on.is_(None))
    return list(session.scalars(stmt.order_by(Project.code).limit(limit)))


def _check_references(session: Session, data: ProjectData, own_id: int | None) -> None:
    taken = session.scalar(
        select(Project.id).where(
            Project.kind == ProjectKind.PROJECT.value,
            func.upper(Project.code) == data.code.upper(),
            Project.id != (own_id or 0),
        )
    )
    if taken:
        raise DuplicateCodeError(f"Ya existe un proyecto con ese {label('code').lower()}.")
    if data.parent_id is not None:
        if data.parent_id == own_id:
            raise ProjectError("Un proyecto no puede ser su propio subproyecto.")
        if session.get(Project, data.parent_id) is None:
            raise ProjectError(f"{label('parent_id')}: no se encontró ese proyecto.")


def create_project(session: Session, data: ProjectData, *, actor_id: int | None) -> Project:
    """Create a project and copy the steps of its phase template (default if none given)."""
    _check_references(session, data, None)
    template = _template(session, data.phase_template_id)
    values = data.model_dump()
    values["phase_template_id"] = template.id if template else None
    with transaction(session):
        project = Project(kind=ProjectKind.PROJECT.value, created_by_id=actor_id, **values)
        session.add(project)
        session.flush()
        if template is not None:
            for step in template.steps:
                session.add(
                    ProjectPhase(
                        project_id=project.id,
                        template_step_id=step.id,
                        **{name: getattr(step, name) for name in _STEP_FIELDS},
                    )
                )
        audit.record(
            session,
            action=AuditAction.CREATE,
            entity_type=ENTITY,
            entity_id=project.id,
            actor_id=actor_id,
            changes=data.model_dump(mode="json"),
        )
    return project


def _template(session: Session, template_id: int | None) -> PhaseTemplate | None:
    if template_id is not None:
        template = session.get(PhaseTemplate, template_id)
        if template is None or not template.is_active:
            raise ProjectError(f"{label('phase_template_id')}: no se encontró esa plantilla.")
        return template
    return session.scalars(
        select(PhaseTemplate).where(PhaseTemplate.is_default, PhaseTemplate.is_active)
    ).first()


def update_project(
    session: Session, project_id: int, data: ProjectData, *, actor_id: int | None
) -> Project:
    """Edit a project. Its template is fixed once phases exist, so it is never changed here."""
    project = get_project(session, project_id)
    _check_references(session, data, project.id)
    values = data.model_dump(exclude={"phase_template_id"})
    with transaction(session):
        before = {key: getattr(project, key) for key in values}
        for key, value in values.items():
            setattr(project, key, value)
        changes = audit.diff(before, values)
        if changes:
            audit.record(
                session,
                action=AuditAction.UPDATE,
                entity_type=ENTITY,
                entity_id=project.id,
                actor_id=actor_id,
                changes=changes,
            )
    return project


def update_phase(
    session: Session, phase_id: int, data: PhaseData, *, actor_id: int | None
) -> ProjectPhase:
    phase = session.get(ProjectPhase, phase_id)
    if phase is None:
        raise ProjectError("No se encontró esa fase.")
    values: Mapping[str, Any] = {
        **data.model_dump(),
        "status": data.status.value,
    }
    with transaction(session):
        before = {key: getattr(phase, key) for key in values}
        for key, value in values.items():
            setattr(phase, key, value)
        changes = audit.diff(before, values)
        if changes:
            audit.record(
                session,
                action=AuditAction.UPDATE,
                entity_type=PHASE_ENTITY,
                entity_id=phase.id,
                actor_id=actor_id,
                changes=changes,
            )
    return phase


def planned_span(project: Project) -> tuple[date, date] | None:
    """First planned start and last planned end across the project's phases."""
    starts = [p.planned_start for p in project.phases if p.planned_start]
    ends = [p.planned_end for p in project.phases if p.planned_end]
    if not starts or not ends:
        return None
    return min(starts), max(ends)


def duration_warning(session: Session, project: Project, today: date) -> str | None:
    """Non-blocking suggestion to split a project planned for longer than the policy allows."""
    return span_warning(session, planned_span(project), today)


def span_warning(session: Session, span: tuple[date, date] | None, today: date) -> str | None:
    policy = policies.policy_at(session, WorkPolicy, today)
    if span is None or policy is None:
        return None
    weeks = ((span[1] - span[0]).days + 1) / 7
    if weeks <= policy.max_project_weeks:
        return None
    return (
        f"El plan dura {weeks:.1f} semanas y la regla es de {policy.max_project_weeks}. "
        "Conviene dividirlo en subproyectos para mostrar avance."
    )
