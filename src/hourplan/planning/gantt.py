"""Gantt views built on the server: by project, by resource, and a personal one.

A view is a :class:`Timeline` plus rows of bars. Templates draw bars as SVG (positions are
attributes, so the strict CSP needs no inline styles) and the same rows export to Excel.
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.catalogs.models import CatalogItem
from hourplan.planning.models import PlanAllocation, PlanVersion
from hourplan.planning.services import capacity, current_allocations, current_version_ids
from hourplan.projects.models import Project, ProjectKind, ProjectPhase
from hourplan.timesheet.models import Absence, ApprovalStatus, TimeEntry

KIND_LABELS = {
    "plan": "Planificado",
    "actual": "Real (horas registradas)",
    "bucket": "Cubo de actividad",
    "absence": "Ausencia",
    "pending": "Ausencia pendiente",
    "overload": "Semana sobreasignada",
}
# Consecutive logged days closer than this become a single "actual" bar (bridges weekends).
_RUN_GAP_DAYS = 3


@dataclass(frozen=True)
class Bar:
    start: date
    end: date
    label: str
    kind: str
    hours: Decimal | None = None
    lane: int = 0

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)


@dataclass
class Row:
    label: str
    bars: list[Bar] = field(default_factory=list)
    level: int = 0
    project_id: int | None = None
    markers: list[Bar] = field(default_factory=list)

    @property
    def lanes(self) -> int:
        return max((b.lane for b in self.bars), default=0) + 1

    @property
    def hours(self) -> Decimal:
        return sum((b.hours or Decimal(0) for b in self.bars if b.kind != "actual"), Decimal(0))


@dataclass(frozen=True)
class Timeline:
    start: date
    end: date

    @classmethod
    def weeks_from(cls, day: date, count: int) -> Timeline:
        monday = day - timedelta(days=day.weekday())
        return cls(monday, monday + timedelta(weeks=count, days=-1))

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def overlaps(self, bar: Bar) -> bool:
        return bar.start <= self.end and bar.end >= self.start

    def x(self, day: date) -> float:
        """Percentage from the left edge to the start of ``day``."""
        return round(100 * (day - self.start).days / self.days, 3)

    def span(self, bar: Bar) -> tuple[float, float]:
        """Left edge and width (percentages) of a bar, clipped to the timeline."""
        first = max(bar.start, self.start)
        last = min(bar.end, self.end)
        left = self.x(first)
        return left, round(max(self.x(last + timedelta(days=1)) - left, 0.4), 3)

    def weeks(self) -> list[tuple[date, date]]:
        monday = self.start - timedelta(days=self.start.weekday())
        result = []
        while monday <= self.end:
            result.append((monday, monday + timedelta(days=6)))
            monday += timedelta(weeks=1)
        return result

    def ticks(self) -> list[tuple[float, date]]:
        return [(self.x(max(a, self.start)), a) for a, _ in self.weeks()]


def stack(bars: Iterable[Bar]) -> list[Bar]:
    """Place overlapping bars on separate lanes so none hides another."""
    lanes: list[date] = []
    placed = []
    for bar in sorted(bars, key=lambda b: (b.start, b.end)):
        lane = next((i for i, end in enumerate(lanes) if end < bar.start), len(lanes))
        if lane == len(lanes):
            lanes.append(bar.end)
        else:
            lanes[lane] = bar.end
        placed.append(replace(bar, lane=lane))
    return placed


def _runs(days: dict[date, Decimal], label: str, kind: str = "actual") -> list[Bar]:
    """Group logged days into bars; days closer than the gap join the same bar."""
    bars: list[Bar] = []
    for day in sorted(days):
        last = bars[-1] if bars else None
        if last and (day - last.end).days <= _RUN_GAP_DAYS:
            bars[-1] = replace(last, end=day, hours=(last.hours or Decimal(0)) + days[day])
        else:
            bars.append(Bar(day, day, label, kind, days[day]))
    return bars


def _bucket_names(session: Session) -> dict[int, str]:
    return dict(
        session.execute(
            select(CatalogItem.id, CatalogItem.name).where(CatalogItem.kind == "activity_bucket")
        ).all()
    )


def _absence_bars(session: Session, tl: Timeline, resource_ids: Sequence[int]) -> list[Absence]:
    return list(
        session.scalars(
            select(Absence).where(
                Absence.resource_id.in_(resource_ids),
                Absence.status != ApprovalStatus.REJECTED.value,
                Absence.start_on <= tl.end,
                Absence.end_on >= tl.start,
            )
        )
    )


def _absence_bar(absence: Absence) -> Bar:
    pending = absence.status == ApprovalStatus.PENDING.value
    return Bar(
        absence.start_on,
        absence.end_on,
        "Ausencia (pendiente)" if pending else "Ausencia",
        "pending" if pending else "absence",
    )


# ---------------------------------------------------------------- by project


def project_rows(
    session: Session, tl: Timeline, *, portfolio_id: int | None = None, today: date
) -> list[Row]:
    """A row per open project (planned and actual span) followed by a row per phase."""
    stmt = select(Project).where(
        Project.kind == ProjectKind.PROJECT.value, Project.closed_on.is_(None)
    )
    if portfolio_id:
        stmt = stmt.where(Project.portfolio_id == portfolio_id)
    projects = list(session.scalars(stmt.order_by(Project.code)))
    ids = [p.id for p in projects]
    plan_spans = {
        pid: (a, b)
        for pid, a, b in session.execute(
            select(
                PlanVersion.project_id,
                func.min(PlanAllocation.start_on),
                func.max(PlanAllocation.end_on),
            )
            .join(PlanVersion, PlanVersion.id == PlanAllocation.plan_version_id)
            .where(PlanAllocation.plan_version_id.in_(current_version_ids()))
            .where(PlanVersion.project_id.in_(ids))
            .group_by(PlanVersion.project_id)
        )
    }
    plan_hours = dict(
        session.execute(
            select(PlanVersion.project_id, func.sum(PlanAllocation.hours))
            .join(PlanVersion, PlanVersion.id == PlanAllocation.plan_version_id)
            .where(PlanAllocation.plan_version_id.in_(current_version_ids()))
            .where(PlanVersion.project_id.in_(ids))
            .group_by(PlanVersion.project_id)
        ).all()
    )
    actual_project = {
        pid: (a, b, h)
        for pid, a, b, h in session.execute(
            select(
                TimeEntry.project_id,
                func.min(TimeEntry.work_date),
                func.max(TimeEntry.work_date),
                func.sum(TimeEntry.hours),
            )
            .where(TimeEntry.project_id.in_(ids))
            .group_by(TimeEntry.project_id)
        )
    }
    actual_phase = {
        phase_id: (a, b, h)
        for phase_id, a, b, h in session.execute(
            select(
                TimeEntry.project_phase_id,
                func.min(TimeEntry.work_date),
                func.max(TimeEntry.work_date),
                func.sum(TimeEntry.hours),
            )
            .where(TimeEntry.project_id.in_(ids), TimeEntry.project_phase_id.is_not(None))
            .group_by(TimeEntry.project_phase_id)
        )
    }

    rows: list[Row] = []
    for project in projects:
        phase_rows = [_phase_row(project, phase, actual_phase, today) for phase in project.phases]
        phase_rows = [r for r in phase_rows if any(tl.overlaps(b) for b in r.bars)]
        starts = [p.planned_start for p in project.phases if p.planned_start]
        ends = [p.planned_end for p in project.phases if p.planned_end]
        span = (min(starts), max(ends)) if starts and ends else plan_spans.get(project.id)
        bars = []
        if span:
            bars.append(Bar(span[0], span[1], "Plan", "plan", plan_hours.get(project.id)))
        if project.id in actual_project:
            a, b, hours = actual_project[project.id]
            bars.append(Bar(a, b, "Real", "actual", hours))
        head = Row(f"{project.code} · {project.title}", stack(bars), 0, project.id)
        if any(tl.overlaps(b) for b in head.bars) or phase_rows:
            rows.append(head)
            rows.extend(phase_rows)
    return rows


def _phase_row(
    project: Project,
    phase: ProjectPhase,
    actual: dict[int | None, tuple[date, date, Decimal]],
    today: date,
) -> Row:
    bars = []
    if phase.planned_start and phase.planned_end:
        bars.append(Bar(phase.planned_start, phase.planned_end, phase.name, "plan"))
    if phase.actual_start:
        end = phase.actual_end or max(phase.actual_start, today)
        bars.append(Bar(phase.actual_start, end, phase.name, "actual"))
    elif phase.id in actual:
        a, b, hours = actual[phase.id]
        bars.append(Bar(a, b, phase.name, "actual", hours))
    return Row(phase.name, stack(bars), 1, project.id)


# ---------------------------------------------------------------- by resource


def resource_rows(
    session: Session, tl: Timeline, resource_ids: list[int] | None = None
) -> list[Row]:
    """A row per active resource: allocations, absences and overloaded weeks."""
    loads = capacity(session, tl.start, tl.end, resource_ids)
    ids = [load.resource.id for load in loads]
    labels = _allocation_labels(session)
    bars: dict[int, list[Bar]] = defaultdict(list)
    for allocation in current_allocations(session, tl.start, tl.end, ids):
        name = labels(allocation)
        bars[allocation.resource_id].append(
            Bar(
                allocation.start_on,
                allocation.end_on,
                name,
                "bucket" if allocation.activity_bucket_id else "plan",
                allocation.hours,
            )
        )
    for absence in _absence_bars(session, tl, ids):
        bars[absence.resource_id].append(_absence_bar(absence))

    rows = []
    for load in loads:
        markers = []
        for a, b in tl.weeks():
            days = [d for d in load.available if a <= d <= b]
            free = load.free(days)
            if days and free < 0:
                markers.append(Bar(a, b, f"Sobreasignada {-free:.1f} h", "overload", -free))
        rows.append(Row(load.resource.display_name, stack(bars[load.resource.id]), markers=markers))
    return rows


def _allocation_labels(session: Session) -> Any:
    projects = dict(
        session.execute(
            select(PlanVersion.id, Project.code).join(Project, Project.id == PlanVersion.project_id)
        ).all()
    )
    buckets = _bucket_names(session)

    def name(allocation: PlanAllocation) -> str:
        if allocation.activity_bucket_id:
            return buckets.get(allocation.activity_bucket_id, "Actividad")
        return projects.get(allocation.plan_version_id or 0, "Proyecto")

    return name


# ---------------------------------------------------------------- personal


def personal_rows(session: Session, tl: Timeline, resource_id: int) -> list[Row]:
    """One person's plan and logged hours per project and activity bucket, plus absences."""
    version_projects = dict(session.execute(select(PlanVersion.id, PlanVersion.project_id)).all())
    groups: dict[tuple[str, int], list[Bar]] = defaultdict(list)
    for allocation in current_allocations(session, tl.start, tl.end, [resource_id]):
        if allocation.activity_bucket_id:
            key = ("bucket", allocation.activity_bucket_id)
            kind = "bucket"
        else:
            key = ("project", version_projects.get(allocation.plan_version_id or 0, 0))
            kind = "plan"
        groups[key].append(
            Bar(allocation.start_on, allocation.end_on, "Plan", kind, allocation.hours)
        )

    logged: dict[tuple[str, int], dict[date, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for entry in session.scalars(
        select(TimeEntry).where(
            TimeEntry.resource_id == resource_id,
            TimeEntry.work_date.between(tl.start, tl.end),
        )
    ):
        key = (
            ("bucket", entry.activity_bucket_id)
            if entry.activity_bucket_id
            else ("project", entry.project_id or 0)
        )
        logged[key][entry.work_date] += entry.hours
    for key, days in logged.items():
        groups[key].extend(_runs(days, "Registrado"))

    project_names = {
        p.id: f"{p.code} · {p.title}"
        for p in session.scalars(
            select(Project).where(Project.id.in_([k[1] for k in groups if k[0] == "project"]))
        )
    }
    buckets = _bucket_names(session)
    rows = [
        Row(
            project_names.get(ident, "Proyecto")
            if kind == "project"
            else f"Actividad: {buckets.get(ident, 'Actividad')}",
            stack(bars),
            project_id=ident if kind == "project" else None,
        )
        for (kind, ident), bars in groups.items()
    ]
    rows.sort(key=lambda r: (r.project_id is None, r.label))
    absences = [_absence_bar(a) for a in _absence_bars(session, tl, [resource_id])]
    if absences:
        rows.append(Row("Ausencias", stack(absences)))
    return rows


# ---------------------------------------------------------------- export


_FILLS = {
    "plan": "3B82F6",
    "actual": "059669",
    "bucket": "8B5CF6",
    "absence": "F59E0B",
    "pending": "FCD34D",
    "overload": "EF4444",
}


def to_workbook(title: str, tl: Timeline, rows: Sequence[Row]) -> bytes:
    """One line per bar, with the weeks it covers shaded, ready to open in Excel."""
    book = Workbook()
    book.remove(book.worksheets[0])
    sheet = book.create_sheet("Gantt")
    sheet.append([title, f"Del {tl.start:%d/%m/%Y} al {tl.end:%d/%m/%Y}"])
    sheet["A1"].font = Font(bold=True, size=13)
    weeks = tl.weeks()
    header = ["Fila", "Tipo", "Detalle", "Desde", "Hasta", "Horas"]
    sheet.append(header + [a.strftime("%d/%m") for a, _ in weeks])
    for cell in sheet[2]:
        cell.font = Font(bold=True)
    for row in rows:
        label = ("    " * row.level) + row.label
        items = [*row.bars, *row.markers]
        if not items:
            sheet.append([label])
            continue
        for bar in items:
            sheet.append(
                [
                    label,
                    bar.kind_label,
                    bar.label,
                    bar.start,
                    bar.end,
                    float(bar.hours) if bar.hours is not None else None,
                ]
            )
            line = sheet.max_row
            sheet.cell(line, 4).number_format = "DD/MM/YYYY"
            sheet.cell(line, 5).number_format = "DD/MM/YYYY"
            fill = PatternFill("solid", fgColor=_FILLS.get(bar.kind, "94A3B8"))
            for n, (a, b) in enumerate(weeks, start=len(header) + 1):
                if bar.start <= b and bar.end >= a:
                    sheet.cell(line, n).fill = fill
    widths = {1: 42, 2: 24, 3: 28, 4: 12, 5: 12, 6: 9}
    for col, width in widths.items():
        sheet.column_dimensions[get_column_letter(col)].width = width
    for n in range(len(header) + 1, len(header) + len(weeks) + 1):
        sheet.column_dimensions[get_column_letter(n)].width = 6
    sheet.freeze_panes = "B3"
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()
