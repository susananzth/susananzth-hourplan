"""Agile work inside a project: epics, sprints, stories (tickets) and the status board."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.db import transaction
from hourplan.projects.models import (
    Epic,
    EpicStatus,
    Project,
    ProjectKind,
    Sprint,
    SprintStatus,
)
from hourplan.shared.labels import label
from hourplan.timesheet.models import TimeEntry


class AgileError(ValueError):
    pass


class EpicData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)


class SprintData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    goal: str | None = Field(default=None, max_length=2000)
    start_date: date
    end_date: date

    @model_validator(mode="after")
    def _order(self) -> SprintData:
        if self.end_date < self.start_date:
            raise ValueError("El fin del sprint no puede ser anterior a su inicio.")
        return self


class StoryData(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    code: str = Field(min_length=1, max_length=40)
    title: str = Field(min_length=1, max_length=200)
    epic_id: int | None = None
    sprint_id: int | None = None
    base_effort_hours: Decimal | None = Field(default=None, ge=0, le=100000)

    @model_validator(mode="after")
    def _quarter(self) -> StoryData:
        hours = self.base_effort_hours
        if hours is not None and hours % Decimal("0.25") != 0:
            raise ValueError(f"{label('base_effort_hours')} debe ser múltiplo de 0,25.")
        return self


def _blank_none(value: str | None) -> str | None:
    return value or None


def _project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.kind != ProjectKind.PROJECT.value:
        raise AgileError("No se encontró ese proyecto.")
    return project


def _epic(session: Session, epic_id: int, project_id: int) -> Epic:
    epic = session.get(Epic, epic_id)
    if epic is None or epic.project_id != project_id:
        raise AgileError("Épica: no se encontró en este proyecto.")
    return epic


def _sprint(session: Session, sprint_id: int, project_id: int) -> Sprint:
    sprint = session.get(Sprint, sprint_id)
    if sprint is None or sprint.project_id != project_id:
        raise AgileError("Sprint: no se encontró en este proyecto.")
    return sprint


def _optional_sprint(session: Session, sprint_id: int | None, project_id: int) -> Sprint | None:
    return None if sprint_id is None else _sprint(session, sprint_id, project_id)


def _check_epic(session: Session, epic_id: int | None, project_id: int) -> None:
    if epic_id is not None:
        _epic(session, epic_id, project_id)


def _record(
    session: Session,
    action: AuditAction,
    entity: str,
    entity_id: int,
    actor_id: int | None,
    changes: dict[str, Any],
) -> None:
    audit.record(
        session,
        action=action,
        entity_type=entity,
        entity_id=entity_id,
        actor_id=actor_id,
        changes=changes,
    )


# ------------------------------------------------------------------ epics
def list_epics(session: Session, project_id: int) -> list[Epic]:
    return list(
        session.scalars(select(Epic).where(Epic.project_id == project_id).order_by(Epic.id))
    )


def create_epic(session: Session, project_id: int, data: EpicData, *, actor_id: int | None) -> Epic:
    _project(session, project_id)
    clash = session.scalar(
        select(Epic.id).where(
            Epic.project_id == project_id, func.lower(Epic.name) == data.name.lower()
        )
    )
    if clash:
        raise AgileError("Ya existe una épica con ese nombre en este proyecto.")
    with transaction(session):
        epic = Epic(
            project_id=project_id, name=data.name, description=_blank_none(data.description)
        )
        session.add(epic)
        session.flush()
        _record(
            session, AuditAction.CREATE, "epic", epic.id, actor_id, data.model_dump(mode="json")
        )
    return epic


def set_epic_status(
    session: Session, project_id: int, epic_id: int, status: EpicStatus, *, actor_id: int | None
) -> Epic:
    epic = _epic(session, epic_id, project_id)
    if epic.status != status.value:
        with transaction(session):
            before, epic.status = epic.status, status.value
            _record(
                session,
                AuditAction.UPDATE,
                "epic",
                epic.id,
                actor_id,
                {"status": [before, status.value]},
            )
    return epic


# ------------------------------------------------------------------ sprints
def list_sprints(session: Session, project_id: int) -> list[Sprint]:
    stmt = select(Sprint).where(Sprint.project_id == project_id)
    return list(session.scalars(stmt.order_by(Sprint.start_date, Sprint.id)))


def create_sprint(
    session: Session, project_id: int, data: SprintData, *, actor_id: int | None
) -> Sprint:
    _project(session, project_id)
    clash = session.scalar(
        select(Sprint.id).where(
            Sprint.project_id == project_id, func.lower(Sprint.name) == data.name.lower()
        )
    )
    if clash:
        raise AgileError("Ya existe un sprint con ese nombre en este proyecto.")
    with transaction(session):
        sprint = Sprint(
            project_id=project_id,
            name=data.name,
            goal=_blank_none(data.goal),
            start_date=data.start_date,
            end_date=data.end_date,
        )
        session.add(sprint)
        session.flush()
        _record(
            session, AuditAction.CREATE, "sprint", sprint.id, actor_id, data.model_dump(mode="json")
        )
    return sprint


def _move_sprint(
    session: Session,
    project_id: int,
    sprint_id: int,
    *,
    source: SprintStatus,
    target: SprintStatus,
    actor_id: int | None,
) -> Sprint:
    sprint = _sprint(session, sprint_id, project_id)
    if sprint.status != source.value:
        raise AgileError("El sprint no está en el estado necesario para ese cambio.")
    if target is SprintStatus.ACTIVE:
        other = session.scalar(
            select(Sprint.id).where(
                Sprint.project_id == project_id, Sprint.status == SprintStatus.ACTIVE.value
            )
        )
        if other:
            raise AgileError("Ya hay un sprint activo; ciérralo antes de iniciar otro.")
    with transaction(session):
        sprint.status = target.value
        _record(
            session,
            AuditAction.UPDATE,
            "sprint",
            sprint.id,
            actor_id,
            {"status": [source.value, target.value]},
        )
    return sprint


def start_sprint(
    session: Session, project_id: int, sprint_id: int, *, actor_id: int | None
) -> Sprint:
    return _move_sprint(
        session,
        project_id,
        sprint_id,
        source=SprintStatus.PLANNED,
        target=SprintStatus.ACTIVE,
        actor_id=actor_id,
    )


def close_sprint(
    session: Session, project_id: int, sprint_id: int, *, actor_id: int | None
) -> Sprint:
    """Close the sprint. Its stories keep it as history; unfinished ones can be moved later."""
    return _move_sprint(
        session,
        project_id,
        sprint_id,
        source=SprintStatus.ACTIVE,
        target=SprintStatus.CLOSED,
        actor_id=actor_id,
    )


# ------------------------------------------------------------------ stories
def list_stories(session: Session, project_id: int) -> list[Project]:
    stmt = select(Project).where(
        Project.parent_id == project_id, Project.kind == ProjectKind.TICKET.value
    )
    return list(session.scalars(stmt.order_by(Project.code)))


def _story(session: Session, project_id: int, story_id: int) -> Project:
    story = session.get(Project, story_id)
    if story is None or story.kind != ProjectKind.TICKET.value or story.parent_id != project_id:
        raise AgileError("No se encontró esa historia en este proyecto.")
    return story


def _check_target(sprint: Sprint | None) -> None:
    if sprint is not None and sprint.status == SprintStatus.CLOSED.value:
        raise AgileError("Sprint: no se pueden añadir historias a un sprint cerrado.")


def create_story(
    session: Session, project_id: int, data: StoryData, *, actor_id: int | None
) -> Project:
    _project(session, project_id)
    _check_epic(session, data.epic_id, project_id)
    _check_target(_optional_sprint(session, data.sprint_id, project_id))
    taken = session.scalar(
        select(Project.id).where(
            Project.kind == ProjectKind.TICKET.value, func.upper(Project.code) == data.code.upper()
        )
    )
    if taken:
        raise AgileError(f"Ya existe una historia con ese {label('code').lower()}.")
    with transaction(session):
        story = Project(
            kind=ProjectKind.TICKET.value,
            parent_id=project_id,
            created_by_id=actor_id,
            **data.model_dump(),
        )
        session.add(story)
        session.flush()
        _record(
            session, AuditAction.CREATE, "project", story.id, actor_id, data.model_dump(mode="json")
        )
    return story


def assign_story(
    session: Session,
    project_id: int,
    story_id: int,
    *,
    epic_id: int | None,
    sprint_id: int | None,
    actor_id: int | None,
) -> Project:
    story = _story(session, project_id, story_id)
    _check_epic(session, epic_id, project_id)
    sprint = _optional_sprint(session, sprint_id, project_id)
    if sprint_id != story.sprint_id:
        _check_target(sprint)
    after = {"epic_id": epic_id, "sprint_id": sprint_id}
    with transaction(session):
        changes = audit.diff({k: getattr(story, k) for k in after}, after)
        story.epic_id, story.sprint_id = epic_id, sprint_id
        if changes:
            _record(session, AuditAction.UPDATE, "project", story.id, actor_id, changes)
    return story


def move_story(
    session: Session, project_id: int, story_id: int, status_id: int | None, *, actor_id: int | None
) -> Project:
    """Change a story's status (a board column)."""
    story = _story(session, project_id, story_id)
    with transaction(session):
        changes = audit.diff({"status_id": story.status_id}, {"status_id": status_id})
        story.status_id = status_id
        if changes:
            _record(session, AuditAction.UPDATE, "project", story.id, actor_id, changes)
    return story


# ------------------------------------------------------------------ numbers
@dataclass(frozen=True)
class Totals:
    stories: int
    done: int
    planned: Decimal
    actual: Decimal


def _totals(session: Session, column: Any, project_id: int) -> dict[int | None, Totals]:
    """Stories, finished stories, planned and actual hours grouped by ``column``."""
    stories = list_stories(session, project_id)
    rows = session.execute(
        select(TimeEntry.project_id, func.sum(TimeEntry.hours))
        .where(TimeEntry.project_id.in_([s.id for s in stories] or [0]))
        .group_by(TimeEntry.project_id)
    )
    actual = dict(rows.all())
    out: dict[int | None, Totals] = {}
    for story in stories:
        key = getattr(story, column.key)
        now = out.get(key, Totals(0, 0, Decimal(0), Decimal(0)))
        out[key] = Totals(
            now.stories + 1,
            now.done + (1 if story.closed_on else 0),
            now.planned + (story.base_effort_hours or 0),
            now.actual + actual.get(story.id, Decimal(0)),
        )
    return out


def sprint_totals(session: Session, project_id: int) -> dict[int | None, Totals]:
    return _totals(session, Project.sprint_id, project_id)


def epic_totals(session: Session, project_id: int) -> dict[int | None, Totals]:
    return _totals(session, Project.epic_id, project_id)
