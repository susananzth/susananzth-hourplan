"""Agile work inside a project: epics, sprints, stories (tickets) and the status board."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.db import transaction
from hourplan.projects.models import (
    BoardLimit,
    Epic,
    EpicStatus,
    Project,
    ProjectKind,
    Sprint,
    SprintStatus,
    SprintStory,
)
from hourplan.shared.labels import label
from hourplan.shared.translate import _
from hourplan.timesheet.models import TimeEntry


class AgileError(ValueError):
    """A rule of agile work was broken. The text is Spanish with ``%s`` slots for translation."""

    def __init__(self, text: str, *params: object) -> None:
        super().__init__(text % params if params else text)
        self.text, self.params = text, params

    def translated(self, translate: Callable[[str], str]) -> str:
        return translate(self.text) % self.params if self.params else translate(self.text)


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
    story_points: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _quarter(self) -> StoryData:
        hours = self.base_effort_hours
        if hours is not None and hours % Decimal("0.25") != 0:
            raise ValueError(_("%s debe ser múltiplo de 0,25.") % (label("base_effort_hours"),))
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
    session: Session,
    project_id: int,
    sprint_id: int,
    *,
    carry_to: int | None = None,
    actor_id: int | None,
) -> Sprint:
    """Close the sprint, keep what it held as history and move unfinished stories on.

    Unfinished stories go to ``carry_to`` (a planned sprint of the same project) or, when it
    is ``None``, back to the backlog.
    """
    target = _optional_sprint(session, carry_to, project_id)
    if target is not None and (target.id == sprint_id or target.status != SprintStatus.PLANNED):
        raise AgileError("Las historias sin terminar solo pueden pasar a un sprint planificado.")
    sprint = _move_sprint(
        session,
        project_id,
        sprint_id,
        source=SprintStatus.ACTIVE,
        target=SprintStatus.CLOSED,
        actor_id=actor_id,
    )
    stories = list(
        session.scalars(
            select(Project).where(
                Project.parent_id == project_id,
                Project.kind == ProjectKind.TICKET.value,
                Project.sprint_id == sprint_id,
            )
        )
    )
    with transaction(session):
        for story in stories:
            carried = story.closed_on is None
            session.add(
                SprintStory(
                    sprint_id=sprint_id,
                    story_id=story.id,
                    story_points=story.story_points,
                    planned_hours=story.base_effort_hours,
                    done_on=story.closed_on,
                    carried_to_id=carry_to if carried else None,
                )
            )
            if carried:
                story.sprint_id = carry_to
                _record(
                    session,
                    AuditAction.UPDATE,
                    "project",
                    story.id,
                    actor_id,
                    {"sprint_id": [sprint_id, carry_to]},
                )
    return sprint


def closed_history(session: Session, sprint_id: int) -> list[SprintStory]:
    stmt = select(SprintStory).where(SprintStory.sprint_id == sprint_id)
    return list(session.scalars(stmt.order_by(SprintStory.story_id)))


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
        raise AgileError("Ya existe una historia con ese código.")
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
    story_points: int | None,
    actor_id: int | None,
) -> Project:
    story = _story(session, project_id, story_id)
    _check_epic(session, epic_id, project_id)
    if story_points is not None and not 0 <= story_points <= 100:
        raise AgileError("Puntos: deben estar entre 0 y 100.")
    sprint = _optional_sprint(session, sprint_id, project_id)
    if sprint_id != story.sprint_id:
        _check_target(sprint)
    after = {"epic_id": epic_id, "sprint_id": sprint_id, "story_points": story_points}
    with transaction(session):
        changes = audit.diff({k: getattr(story, k) for k in after}, after)
        story.epic_id, story.sprint_id, story.story_points = epic_id, sprint_id, story_points
        if changes:
            _record(session, AuditAction.UPDATE, "project", story.id, actor_id, changes)
    return story


def _status(session: Session, status_id: int) -> CatalogItem:
    item = session.get(CatalogItem, status_id)
    if item is None or item.kind != CatalogKind.PROJECT_STATUS.value:
        raise AgileError("Estado: no existe.")
    return item


def move_story(
    session: Session,
    project_id: int,
    story_id: int,
    status_id: int | None,
    *,
    actor_id: int | None,
    on: date | None = None,
) -> Project:
    """Change a story's status (a board column).

    A closing status (``is_closed``) marks the story finished on ``on``; leaving it reopens
    the story. A column at its work-in-progress limit takes no more stories.
    """
    story = _story(session, project_id, story_id)
    status = None if status_id is None else _status(session, status_id)
    if status_id is not None and status_id != story.status_id:
        limit = wip_limits(session, project_id).get(status_id)
        held = session.scalar(
            select(func.count())
            .select_from(Project)
            .where(
                Project.parent_id == project_id,
                Project.kind == ProjectKind.TICKET.value,
                Project.status_id == status_id,
            )
        )
        if limit is not None and (held or 0) >= limit:
            raise AgileError(
                "La columna %s ya tiene %s historias, su límite. Termina una antes de mover otra.",
                status.name if status else "",
                limit,
            )
    closing = bool(status and status.attributes.get("is_closed"))
    closed_on = (story.closed_on or on or datetime.now().astimezone().date()) if closing else None
    before = {"status_id": story.status_id, "closed_on": story.closed_on}
    after = {"status_id": status_id, "closed_on": closed_on}
    with transaction(session):
        changes = audit.diff(before, after)
        story.status_id, story.closed_on = status_id, closed_on
        if changes:
            _record(session, AuditAction.UPDATE, "project", story.id, actor_id, changes)
    return story


# ------------------------------------------------------------------ work-in-progress limits
def wip_limits(session: Session, project_id: int) -> dict[int, int]:
    rows = session.execute(
        select(BoardLimit.status_id, BoardLimit.wip_limit).where(
            BoardLimit.project_id == project_id
        )
    )
    return {status_id: limit for status_id, limit in rows.all()}  # noqa: C416


def set_wip_limits(
    session: Session, project_id: int, limits: dict[int, int | None], *, actor_id: int | None
) -> None:
    """Replace the limits of the given columns; ``None`` removes a column's limit."""
    _project(session, project_id)
    for status_id, limit in limits.items():
        _status(session, status_id)
        if limit is not None and not 1 <= limit <= 999:
            raise AgileError("Límite: debe estar entre 1 y 999, o vacío para no tener límite.")
    before = wip_limits(session, project_id)
    merged: dict[int, int | None] = {**before, **limits}
    after = {k: v for k, v in merged.items() if v is not None}
    if before == after:
        return
    with transaction(session):
        for status_id, limit in limits.items():
            row = session.get(BoardLimit, (project_id, status_id))
            if limit is None:
                if row is not None:
                    session.delete(row)
            elif row is None:
                session.add(BoardLimit(project_id=project_id, status_id=status_id, wip_limit=limit))
            else:
                row.wip_limit = limit
        _record(
            session,
            AuditAction.UPDATE,
            "project",
            project_id,
            actor_id,
            {"wip_limits": [_keyed(before), _keyed(after)]},
        )


def _keyed(limits: dict[int, int]) -> dict[str, int]:
    return {str(k): v for k, v in sorted(limits.items())}


# ------------------------------------------------------------------ numbers
@dataclass(frozen=True)
class Totals:
    stories: int
    done: int
    planned: Decimal
    actual: Decimal
    points: int = 0
    points_done: int = 0

    def add(
        self, *, done: bool, planned: Decimal | None, actual: Decimal, points: int | None
    ) -> Totals:
        return Totals(
            self.stories + 1,
            self.done + (1 if done else 0),
            self.planned + (planned or 0),
            self.actual + actual,
            self.points + (points or 0),
            self.points_done + ((points or 0) if done else 0),
        )


EMPTY = Totals(0, 0, Decimal(0), Decimal(0))


def _hours_by_story(
    session: Session, story_ids: list[int], start: date | None = None, end: date | None = None
) -> dict[int, Decimal]:
    stmt = select(TimeEntry.project_id, func.sum(TimeEntry.hours)).where(
        TimeEntry.project_id.in_(story_ids or [0])
    )
    if start is not None and end is not None:
        stmt = stmt.where(TimeEntry.work_date.between(start, end))
    return dict(session.execute(stmt.group_by(TimeEntry.project_id)).all())  # type: ignore[arg-type]


def _totals(session: Session, column: Any, project_id: int) -> dict[int | None, Totals]:
    """Stories, finished stories, planned and actual hours and points grouped by ``column``."""
    stories = list_stories(session, project_id)
    actual = _hours_by_story(session, [s.id for s in stories])
    out: dict[int | None, Totals] = {}
    for story in stories:
        key = getattr(story, column.key)
        out[key] = out.get(key, EMPTY).add(
            done=story.closed_on is not None,
            planned=story.base_effort_hours,
            actual=actual.get(story.id, Decimal(0)),
            points=story.story_points,
        )
    return out


def sprint_totals(session: Session, project_id: int) -> dict[int | None, Totals]:
    """Per sprint. A closed sprint counts what it held at closing, with the hours of its dates."""
    out = _totals(session, Project.sprint_id, project_id)
    for sprint in list_sprints(session, project_id):
        if sprint.status != SprintStatus.CLOSED.value:
            continue
        held = closed_history(session, sprint.id)
        if not held:
            continue
        actual = _hours_by_story(
            session, [h.story_id for h in held], sprint.start_date, sprint.end_date
        )
        totals = EMPTY
        for h in held:
            totals = totals.add(
                done=h.done_on is not None,
                planned=h.planned_hours,
                actual=actual.get(h.story_id, Decimal(0)),
                points=h.story_points,
            )
        out[sprint.id] = totals
    return out


def epic_totals(session: Session, project_id: int) -> dict[int | None, Totals]:
    return _totals(session, Project.epic_id, project_id)


# ------------------------------------------------------------------ burndown
@dataclass(frozen=True)
class Burndown:
    """Work left in a sprint day by day, in points when the stories have them, else hours."""

    unit: str  # "points" or "hours"
    scope: Decimal
    days: list[date]
    ideal: list[Decimal]
    remaining: list[Decimal]  # only up to the last day that has happened


def burndown(session: Session, project_id: int, sprint_id: int, *, today: date) -> Burndown:
    sprint = _sprint(session, sprint_id, project_id)
    if sprint.status == SprintStatus.CLOSED.value and (held := closed_history(session, sprint_id)):
        items = [(h.story_points, h.planned_hours, h.done_on) for h in held]
    else:
        stmt = select(Project).where(
            Project.parent_id == project_id,
            Project.kind == ProjectKind.TICKET.value,
            Project.sprint_id == sprint_id,
        )
        items = [(s.story_points, s.base_effort_hours, s.closed_on) for s in session.scalars(stmt)]
    use_points = any(points for points, _h, _d in items)
    sizes = [
        (Decimal(points or 0) if use_points else (hours or Decimal(0)), done)
        for points, hours, done in items
    ]
    scope = sum((size for size, _d in sizes), Decimal(0))
    days = [
        sprint.start_date + timedelta(days=n)
        for n in range((sprint.end_date - sprint.start_date).days + 1)
    ]
    steps = max(len(days) - 1, 1)
    ideal = [(scope - scope * n / steps).quantize(Decimal("0.01")) for n in range(len(days))]
    last = sprint.end_date if sprint.status == SprintStatus.CLOSED.value else today
    remaining = [
        scope - sum((size for size, done in sizes if done is not None and done <= day), Decimal(0))
        for day in days
        if day <= last
    ]
    return Burndown("points" if use_points else "hours", scope, days, ideal, remaining)
