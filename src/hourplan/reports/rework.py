"""Which time entries are rework, and the aggregate query scaffolding that uses it.

An entry is rework when it was corrected by hand (``is_rework``), or when it is construction
work in a project dated on or after the project's first returned validation round.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, case, false, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from hourplan.projects.models import CycleOutcome, PhaseCycle, PhaseType, ProjectPhase
from hourplan.timesheet.models import TimeEntry


@dataclass(frozen=True)
class ReworkScope:
    """``is_rework`` is a boolean expression; ``attach`` adds the joins it needs."""

    is_rework: ColumnElement[bool]
    _first_return: Any

    def attach(self, stmt: Any) -> Any:
        return (
            stmt.select_from(TimeEntry)
            .outerjoin(ProjectPhase, ProjectPhase.id == TimeEntry.project_phase_id)
            .outerjoin(self._first_return, self._first_return.c.project_id == TimeEntry.project_id)
        )

    def rework_hours(self) -> ColumnElement[Any]:
        return func.coalesce(func.sum(case((self.is_rework, TimeEntry.hours), else_=0)), 0)

    def total_hours(self) -> ColumnElement[Any]:
        return func.coalesce(func.sum(TimeEntry.hours), 0)


def scope() -> ReworkScope:
    first_return = (
        select(
            ProjectPhase.project_id.label("project_id"),
            func.min(PhaseCycle.resolved_on).label("returned_on"),
        )
        .join(PhaseCycle, PhaseCycle.project_phase_id == ProjectPhase.id)
        .where(PhaseCycle.outcome == CycleOutcome.RETURNED.value)
        .group_by(ProjectPhase.project_id)
        .subquery("first_return")
    )
    derived = func.coalesce(
        and_(
            ProjectPhase.phase_type == PhaseType.CONSTRUCTION.value,
            first_return.c.returned_on.is_not(None),
            TimeEntry.work_date >= first_return.c.returned_on,
        ),
        false(),
    )
    is_rework = case((TimeEntry.is_rework.is_not(None), TimeEntry.is_rework), else_=derived)
    return ReworkScope(is_rework, first_return)


def returned_cycles(session: Session, project_ids: set[int]) -> dict[int, list[PhaseCycle]]:
    """Returned rounds per project, oldest first."""
    if not project_ids:
        return {}
    rows = session.execute(
        select(ProjectPhase.project_id, PhaseCycle)
        .join(PhaseCycle, PhaseCycle.project_phase_id == ProjectPhase.id)
        .where(
            ProjectPhase.project_id.in_(project_ids),
            PhaseCycle.outcome == CycleOutcome.RETURNED.value,
        )
        .order_by(PhaseCycle.resolved_on)
    )
    result: dict[int, list[PhaseCycle]] = {}
    for project_id, cycle in rows:
        result.setdefault(project_id, []).append(cycle)
    return result
