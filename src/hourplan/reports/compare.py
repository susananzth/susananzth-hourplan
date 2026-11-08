"""Plan versus actual: hours, dates, rework and compliance of a project.

Planned = the baseline if one was set, otherwise the latest plan version. Actual hours are
always a sum of time entries. Compliance follows the evaluation policy: within the tolerance is
green, up to the red limit amber, beyond red (over- and under-runs alike); with colours off only
the figure remains.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.catalogs.models import CatalogItem
from hourplan.db import transaction
from hourplan.planning.models import PlanAllocation, PlanVersion
from hourplan.planning.services import current_version_ids
from hourplan.policies import services as policies
from hourplan.policies.models import EvaluationPolicy
from hourplan.projects.models import (
    LinkType,
    PhaseCycle,
    Project,
    ProjectKind,
    ProjectPhase,
    WorkItemLink,
)
from hourplan.reports import rework
from hourplan.reports.models import Compliance, ComplianceSnapshot
from hourplan.timesheet.models import TimeEntry

ZERO = Decimal(0)
NO_CATEGORY = "Sin categoría de origen"


class ReportError(ValueError):
    pass


def deviation_pct(planned: Decimal | int | None, actual: Decimal | int) -> Decimal | None:
    if not planned or planned <= 0:
        return None
    return ((Decimal(actual) - planned) / planned * 100).quantize(Decimal("0.01"))


def classify(pct: Decimal | None, tolerance: Decimal, red: Decimal) -> Compliance:
    size = abs(pct or ZERO)
    if size <= tolerance:
        return Compliance.GREEN
    return Compliance.AMBER if size <= red else Compliance.RED


@dataclass(frozen=True)
class Evaluation:
    """Figures and (when colours are on) status of one project under one policy."""

    planned_hours: Decimal | None
    actual_hours: Decimal
    rework_hours: Decimal
    planned_days: int | None
    actual_days: int | None
    hours_pct: Decimal | None
    dates_pct: Decimal | None
    hours_status: Compliance | None
    dates_status: Compliance | None
    policy: EvaluationPolicy | None
    colors_enabled: bool

    @property
    def rework_pct(self) -> Decimal | None:
        if not self.actual_hours:
            return None
        return (self.rework_hours / self.actual_hours * 100).quantize(Decimal("0.1"))


def evaluate(
    policy: EvaluationPolicy | None,
    *,
    planned_hours: Decimal | None,
    actual_hours: Decimal,
    rework_hours: Decimal,
    planned_days: int | None,
    actual_days: int | None,
) -> Evaluation:
    hours_pct = deviation_pct(planned_hours, actual_hours)
    dates_pct = deviation_pct(planned_days, actual_days) if actual_days is not None else None
    colors = bool(policy and policy.colors_enabled)
    hours_status = dates_status = None
    if policy and colors:
        if hours_pct is not None:
            hours_status = classify(hours_pct, policy.hours_tolerance_pct, policy.hours_red_pct)
        if dates_pct is not None:
            dates_status = classify(dates_pct, policy.dates_tolerance_pct, policy.dates_red_pct)
    return Evaluation(
        planned_hours,
        actual_hours,
        rework_hours,
        planned_days,
        actual_days,
        hours_pct,
        dates_pct,
        hours_status,
        dates_status,
        policy,
        colors,
    )


def from_snapshot(snapshot: ComplianceSnapshot, policy: EvaluationPolicy | None) -> Evaluation:
    return Evaluation(
        snapshot.planned_hours,
        snapshot.actual_hours,
        snapshot.rework_hours,
        snapshot.planned_days,
        snapshot.actual_days,
        snapshot.hours_deviation_pct,
        snapshot.dates_deviation_pct,
        Compliance(snapshot.hours_status) if snapshot.hours_status else None,
        Compliance(snapshot.dates_status) if snapshot.dates_status else None,
        policy,
        snapshot.colors_enabled,
    )


def _days(start: date | None, end: date | None) -> int | None:
    return (end - start).days + 1 if start and end else None


# ---------------------------------------------------------------- totals in bulk


@dataclass(frozen=True)
class PlanTotals:
    version: PlanVersion
    hours: Decimal
    start: date | None
    end: date | None


@dataclass(frozen=True)
class ActualTotals:
    hours: Decimal
    rework: Decimal
    first: date | None
    last: date | None


def reference_versions(session: Session, project_ids: list[int]) -> dict[int, PlanVersion]:
    """The baseline of each project, or its latest version when it has none."""
    result: dict[int, PlanVersion] = {}
    versions = session.scalars(
        select(PlanVersion)
        .where(PlanVersion.project_id.in_(project_ids))
        .order_by(PlanVersion.project_id, PlanVersion.number)
    )
    for version in versions:
        current = result.get(version.project_id)
        # Versions come oldest first: a later one wins unless a baseline already holds the slot.
        if current is None or version.is_baseline or not current.is_baseline:
            result[version.project_id] = version
    return result


def plan_totals(session: Session, project_ids: list[int]) -> dict[int, PlanTotals]:
    versions = reference_versions(session, project_ids)
    if not versions:
        return {}
    rows = session.execute(
        select(
            PlanAllocation.plan_version_id,
            func.sum(PlanAllocation.hours),
            func.min(PlanAllocation.start_on),
            func.max(PlanAllocation.end_on),
        )
        .where(PlanAllocation.plan_version_id.in_([v.id for v in versions.values()]))
        .group_by(PlanAllocation.plan_version_id)
    )
    by_version = {row[0]: row for row in rows}
    result = {}
    for project_id, version in versions.items():
        row = by_version.get(version.id)
        if row:
            result[project_id] = PlanTotals(version, Decimal(row[1]), row[2], row[3])
    return result


def actual_totals(session: Session, project_ids: list[int]) -> dict[int, ActualTotals]:
    sc = rework.scope()
    stmt = sc.attach(
        select(
            TimeEntry.project_id,
            sc.total_hours(),
            sc.rework_hours(),
            func.min(TimeEntry.work_date),
            func.max(TimeEntry.work_date),
        )
        .where(TimeEntry.project_id.in_(project_ids))
        .group_by(TimeEntry.project_id)
    )
    return {
        row[0]: ActualTotals(Decimal(row[1]), Decimal(row[2]), row[3], row[4])
        for row in session.execute(stmt)
    }


# ---------------------------------------------------------------- one project


@dataclass(frozen=True)
class PhaseLine:
    phase: ProjectPhase | None
    planned_hours: Decimal
    actual_hours: Decimal
    rework_hours: Decimal
    actual_start: date | None
    actual_end: date | None


@dataclass
class ProjectReport:
    project: Project
    plan: PlanTotals | None
    actual: ActualTotals
    evaluation: Evaluation
    phases: list[PhaseLine] = field(default_factory=list)
    rework_by_category: list[tuple[str, Decimal]] = field(default_factory=list)
    ticket_hours: Decimal = ZERO
    ticket_count: int = 0

    @property
    def initial_hours(self) -> Decimal:
        return self.actual.hours - self.actual.rework


def _spans(
    project: Project, plan: PlanTotals | None, actual: ActualTotals
) -> tuple[int | None, int | None]:
    phases = [p for p in project.phases if p.planned_start and p.planned_end]
    if plan and plan.start and plan.end:
        planned = _days(plan.start, plan.end)
    elif phases:
        planned = _days(
            min(p.planned_start for p in phases if p.planned_start),
            max(p.planned_end for p in phases if p.planned_end),
        )
    else:
        planned = None
    return planned, _days(actual.first, actual.last)


def project_evaluation(
    project: Project,
    plan: PlanTotals | None,
    actual: ActualTotals,
    policy: EvaluationPolicy | None,
) -> Evaluation:
    planned_days, actual_days = _spans(project, plan, actual)
    return evaluate(
        policy,
        planned_hours=plan.hours if plan else None,
        actual_hours=actual.hours,
        rework_hours=actual.rework,
        planned_days=planned_days,
        actual_days=actual_days,
    )


def _phase_lines(session: Session, project: Project, plan: PlanTotals | None) -> list[PhaseLine]:
    sc = rework.scope()
    stmt = sc.attach(
        select(
            TimeEntry.project_phase_id,
            sc.total_hours(),
            sc.rework_hours(),
            func.min(TimeEntry.work_date),
            func.max(TimeEntry.work_date),
        )
        .where(TimeEntry.project_id == project.id)
        .group_by(TimeEntry.project_phase_id)
    )
    actual = {row[0]: row for row in session.execute(stmt)}
    planned: dict[int | None, Decimal] = {}
    if plan:
        planned = {
            row[0]: Decimal(row[1])
            for row in session.execute(
                select(PlanAllocation.project_phase_id, func.sum(PlanAllocation.hours))
                .where(PlanAllocation.plan_version_id == plan.version.id)
                .group_by(PlanAllocation.project_phase_id)
            )
        }
    lines = []
    for phase in [*project.phases, None]:
        key = phase.id if phase else None
        row = actual.get(key)
        hours = planned.get(key, ZERO)
        if phase is None and not row and not hours:
            continue
        lines.append(
            PhaseLine(
                phase,
                hours,
                Decimal(row[1]) if row else ZERO,
                Decimal(row[2]) if row else ZERO,
                row[3] if row else (phase.actual_start if phase else None),
                row[4] if row else (phase.actual_end if phase else None),
            )
        )
    return lines


def rework_by_category(
    session: Session,
    *,
    start: date | None = None,
    end: date | None = None,
    project_id: int | None = None,
    resource_id: int | None = None,
) -> list[tuple[str, Decimal]]:
    """Rework hours per process-origin category (never per person).

    An entry takes its own category, otherwise that of the latest returned round before it.
    """
    sc = rework.scope()
    stmt = sc.attach(
        select(
            TimeEntry.project_id,
            TimeEntry.work_date,
            TimeEntry.hours,
            TimeEntry.rework_category_id,
        ).where(sc.is_rework, TimeEntry.project_id.is_not(None))
    )
    if start:
        stmt = stmt.where(TimeEntry.work_date >= start)
    if end:
        stmt = stmt.where(TimeEntry.work_date <= end)
    if project_id:
        stmt = stmt.where(TimeEntry.project_id == project_id)
    if resource_id:
        stmt = stmt.where(TimeEntry.resource_id == resource_id)
    entries = session.execute(stmt).all()
    cycles = rework.returned_cycles(session, {e[0] for e in entries if e[0] is not None})
    totals: dict[int | None, Decimal] = {}
    for pid, day, hours, own in entries:
        if pid is None:
            continue
        category = own
        if category is None:
            earlier: list[PhaseCycle] = [
                c for c in cycles.get(pid, []) if c.resolved_on and c.resolved_on <= day
            ]
            if earlier:
                category = earlier[-1].rework_category_id
        totals[category] = totals.get(category, ZERO) + hours
    names = {
        item.id: item.name
        for item in session.scalars(
            select(CatalogItem).where(CatalogItem.id.in_([k for k in totals if k]))
        )
    }
    rows = [(names.get(k, NO_CATEGORY) if k else NO_CATEGORY, v) for k, v in totals.items()]
    return sorted(rows, key=lambda row: row[0])


def _tickets(session: Session, project_id: int) -> tuple[Decimal, int]:
    """Hours and number of tickets caused by the project (post-production rework)."""
    ticket_ids = select(WorkItemLink.source_id).where(
        WorkItemLink.target_id == project_id, WorkItemLink.link_type == LinkType.CAUSED_BY.value
    )
    hours = session.scalar(
        select(func.coalesce(func.sum(TimeEntry.hours), 0)).where(
            TimeEntry.project_id.in_(ticket_ids)
        )
    )
    count = session.scalar(select(func.count()).select_from(ticket_ids.subquery()))
    return Decimal(hours or 0), int(count or 0)


def get_snapshot(session: Session, project_id: int) -> ComplianceSnapshot | None:
    return session.scalars(
        select(ComplianceSnapshot).where(ComplianceSnapshot.project_id == project_id)
    ).first()


def _project(session: Session, project_id: int) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise ReportError("No se encontró ese proyecto.")
    return project


def project_report(
    session: Session, project_id: int, *, policy: EvaluationPolicy | None
) -> ProjectReport:
    project = _project(session, project_id)
    plan = plan_totals(session, [project_id]).get(project_id)
    actual = actual_totals(session, [project_id]).get(
        project_id, ActualTotals(ZERO, ZERO, None, None)
    )
    hours, count = _tickets(session, project_id)
    return ProjectReport(
        project=project,
        plan=plan,
        actual=actual,
        evaluation=project_evaluation(project, plan, actual, policy),
        phases=_phase_lines(session, project, plan),
        rework_by_category=rework_by_category(session, project_id=project_id),
        ticket_hours=hours,
        ticket_count=count,
    )


# ---------------------------------------------------------------- list


@dataclass(frozen=True)
class Summary:
    project: Project
    evaluation: Evaluation
    snapshot: ComplianceSnapshot | None
    plan_source: str | None  # "baseline" | "current" | None


def summaries(
    session: Session,
    *,
    portfolio_id: int | None = None,
    include_closed: bool = True,
    today: date,
) -> list[Summary]:
    """One line per project. Closed projects show their frozen evaluation when they have one."""
    stmt = select(Project).where(Project.kind == ProjectKind.PROJECT.value)
    if portfolio_id:
        stmt = stmt.where(Project.portfolio_id == portfolio_id)
    if not include_closed:
        stmt = stmt.where(Project.closed_on.is_(None))
    projects = list(session.scalars(stmt.order_by(Project.code)))
    ids = [p.id for p in projects]
    plans = plan_totals(session, ids)
    actuals = actual_totals(session, ids)
    snapshots = {
        s.project_id: s
        for s in session.scalars(
            select(ComplianceSnapshot).where(ComplianceSnapshot.project_id.in_(ids))
        )
    }
    result = []
    for project in projects:
        plan = plans.get(project.id)
        snapshot = snapshots.get(project.id)
        if snapshot:
            policy = (
                session.get(EvaluationPolicy, snapshot.policy_id) if snapshot.policy_id else None
            )
            evaluation = from_snapshot(snapshot, policy)
        else:
            policy = policies.policy_at(session, EvaluationPolicy, project.closed_on or today)
            actual = actuals.get(project.id, ActualTotals(ZERO, ZERO, None, None))
            evaluation = project_evaluation(project, plan, actual, policy)
        source = None
        if plan:
            source = "baseline" if plan.version.is_baseline else "current"
        result.append(Summary(project, evaluation, snapshot, source))
    return result


# ---------------------------------------------------------------- closing


def save_snapshot(session: Session, project_id: int, *, actor_id: int | None) -> ComplianceSnapshot:
    """Freeze the compliance of a closed project with the policy in force on its closing day."""
    project = _project(session, project_id)
    if project.closed_on is None:
        raise ReportError("El proyecto debe estar cerrado para guardar su evaluación.")
    if get_snapshot(session, project_id):
        raise ReportError("Este proyecto ya tiene su evaluación guardada.")
    policy = policies.policy_at(session, EvaluationPolicy, project.closed_on)
    report = project_report(session, project_id, policy=policy)
    ev = report.evaluation
    with transaction(session):
        snapshot = ComplianceSnapshot(
            project_id=project_id,
            evaluated_on=project.closed_on,
            policy_id=policy.id if policy else None,
            colors_enabled=ev.colors_enabled,
            planned_hours=ev.planned_hours,
            actual_hours=ev.actual_hours,
            rework_hours=ev.rework_hours,
            hours_deviation_pct=ev.hours_pct,
            hours_status=ev.hours_status.value if ev.hours_status else None,
            planned_days=ev.planned_days,
            actual_days=ev.actual_days,
            dates_deviation_pct=ev.dates_pct,
            dates_status=ev.dates_status.value if ev.dates_status else None,
            created_by_id=actor_id,
        )
        session.add(snapshot)
        session.flush()
        audit.record(
            session,
            action=AuditAction.CREATE,
            entity_type="compliance_snapshot",
            entity_id=snapshot.id,
            actor_id=actor_id,
            changes={"project_id": project_id, "evaluated_on": project.closed_on},
        )
    return snapshot


def close_project(
    session: Session, project_id: int, closed_on: date, *, actor_id: int | None
) -> ComplianceSnapshot:
    project = _project(session, project_id)
    if project.closed_on is not None:
        raise ReportError("Este proyecto ya está cerrado.")
    project.closed_on = closed_on
    audit.record(
        session,
        action=AuditAction.UPDATE,
        entity_type="project",
        entity_id=project_id,
        actor_id=actor_id,
        changes={"closed_on": [None, closed_on]},
    )
    return save_snapshot(session, project_id, actor_id=actor_id)


def recalculate(session: Session, project_id: int, today: date) -> Evaluation:
    """Explicit report: the project judged by the policy in force today, nothing stored."""
    policy = policies.policy_at(session, EvaluationPolicy, today)
    return project_report(session, project_id, policy=policy).evaluation


# ---------------------------------------------------------------- one person


@dataclass(frozen=True)
class PersonalLine:
    project: Project
    planned_hours: Decimal
    actual_hours: Decimal
    rework_hours: Decimal


def personal_lines(session: Session, resource_id: int) -> list[PersonalLine]:
    """A person's own planned (latest plan) versus actual hours per project."""
    planned: dict[int, Decimal] = {
        row[0]: Decimal(row[1])
        for row in session.execute(
            select(PlanVersion.project_id, func.sum(PlanAllocation.hours))
            .join(PlanAllocation, PlanAllocation.plan_version_id == PlanVersion.id)
            .where(
                PlanAllocation.resource_id == resource_id,
                PlanVersion.id.in_(current_version_ids()),
            )
            .group_by(PlanVersion.project_id)
        )
    }
    sc = rework.scope()
    actual: dict[Any, tuple[Decimal, Decimal]] = {
        row[0]: (Decimal(row[1]), Decimal(row[2]))
        for row in session.execute(
            sc.attach(
                select(TimeEntry.project_id, sc.total_hours(), sc.rework_hours())
                .where(TimeEntry.resource_id == resource_id, TimeEntry.project_id.is_not(None))
                .group_by(TimeEntry.project_id)
            )
        )
    }
    ids = set(planned) | set(actual)
    projects = {p.id: p for p in session.scalars(select(Project).where(Project.id.in_(ids))).all()}
    return [
        PersonalLine(projects[pid], planned.get(pid, ZERO), *actual.get(pid, (ZERO, ZERO)))
        for pid in sorted(ids, key=lambda i: projects[i].code)
    ]
