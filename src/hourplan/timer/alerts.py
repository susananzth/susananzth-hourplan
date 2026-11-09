"""Alerts when a project has consumed a share of its hours.

Thresholds come from the project template; each person may replace them for themselves.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.db import transaction
from hourplan.projects.models import Project
from hourplan.shared.translate import _
from hourplan.timer import services as timer_services
from hourplan.timer.models import AlertFired, AlertSetting
from hourplan.timesheet.models import TimeEntry

MAX_PERCENT = 300
LOOKBACK_DAYS = 90


class AlertError(ValueError):
    pass


def parse_thresholds(raw: str) -> list[int]:
    """'50, 75 90' -> [50, 75, 90]. Blank = none."""
    values: set[int] = set()
    for part in raw.replace(",", " ").replace(";", " ").split():
        try:
            number = int(part.rstrip("%"))
        except ValueError:
            raise AlertError(
                _("«%s» no es un porcentaje. Escribe, por ejemplo, 50, 75, 90.") % (part,)
            ) from None
        if not 1 <= number <= MAX_PERCENT:
            raise AlertError(_("Cada porcentaje debe estar entre 1 y %s.") % (MAX_PERCENT,))
        values.add(number)
    return sorted(values)


def _setting(session: Session, project_id: int, resource_id: int | None) -> AlertSetting | None:
    return session.scalars(
        select(AlertSetting).where(
            AlertSetting.project_id == project_id,
            AlertSetting.resource_id.is_not_distinct_from(resource_id),
        )
    ).first()


def template(session: Session, project_id: int) -> list[int]:
    found = _setting(session, project_id, None)
    return list(found.thresholds) if found else []


def personal(session: Session, project_id: int, resource_id: int) -> list[int] | None:
    """None = no personal change, so the template rules."""
    found = _setting(session, project_id, resource_id)
    return list(found.thresholds) if found else None


def effective(session: Session, project_id: int, resource_id: int) -> list[int]:
    mine = personal(session, project_id, resource_id)
    return mine if mine is not None else template(session, project_id)


def can_edit_template(project: Project, user_id: int, *, is_admin: bool) -> bool:
    return is_admin or user_id in (project.manager_id, project.lead_id)


def set_thresholds(
    session: Session, project_id: int, resource_id: int | None, thresholds: list[int] | None
) -> None:
    """Save the template (resource None) or a personal list; None removes the personal change."""
    if session.get(Project, project_id) is None:
        raise AlertError("No se encontró el proyecto.")
    with transaction(session):
        found = _setting(session, project_id, resource_id)
        if thresholds is None:
            if found is not None:
                session.delete(found)
            return
        if found is None:
            session.add(
                AlertSetting(project_id=project_id, resource_id=resource_id, thresholds=thresholds)
            )
        else:
            found.thresholds = thresholds


def budget(project: Project) -> Decimal | None:
    total = (project.base_effort_hours or 0) + (project.contingency_hours or 0)
    return Decimal(total) if total > 0 else None


def worked_hours(session: Session, project_id: int) -> Decimal:
    return Decimal(
        session.scalar(
            select(func.coalesce(func.sum(TimeEntry.hours), 0)).where(
                TimeEntry.project_id == project_id
            )
        )
        or 0
    )


@dataclass(frozen=True)
class Alert:
    project_id: int
    code: str
    title: str
    percent: int
    consumed_pct: int


def pending(
    session: Session, resource_id: int, now: datetime, today: date | None = None
) -> list[Alert]:
    """Thresholds newly crossed in the projects this person works on; each is reported once.

    The running timer counts as hours already worked, so the alert arrives while it runs.
    """
    today = today or now.astimezone().date()
    since = today - timedelta(days=LOOKBACK_DAYS)
    project_ids = {
        pid
        for pid in session.scalars(
            select(TimeEntry.project_id)
            .where(
                TimeEntry.resource_id == resource_id,
                TimeEntry.project_id.is_not(None),
                TimeEntry.work_date >= since,
            )
            .distinct()
        )
        if pid is not None
    }
    live: dict[int, int] = {}
    for item in timer_services.open_timers(session, resource_id, now):
        if item.project_id is not None:
            project_ids.add(item.project_id)
            live[item.project_id] = live.get(item.project_id, 0) + timer_services.elapsed_seconds(
                item, now
            )
    result: list[Alert] = []
    for project_id in sorted(project_ids):
        project = session.get(Project, project_id)
        total = budget(project) if project and project.closed_on is None else None
        thresholds = effective(session, project_id, resource_id)
        if project is None or total is None or not thresholds:
            continue
        hours = worked_hours(session, project_id) + Decimal(live.get(project_id, 0)) / 3600
        pct = int(hours * 100 / total)
        crossed = [t for t in thresholds if t <= pct]
        done = set(
            session.scalars(
                select(AlertFired.percent).where(
                    AlertFired.project_id == project_id, AlertFired.resource_id == resource_id
                )
            )
        )
        new = [t for t in crossed if t not in done]
        if not new:
            continue
        with transaction(session):
            session.add_all(
                AlertFired(project_id=project_id, resource_id=resource_id, percent=t) for t in new
            )
        result.append(Alert(project_id, project.code, project.title, max(new), pct))
    return result
