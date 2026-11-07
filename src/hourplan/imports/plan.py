"""Plan import: the backlog sheet (projects, phases, effort) and the planning sheet (hours
planned per resource and day, stored as ``plan_allocation`` runs).

Each project whose planned hours changed gets a new plan version (the first is the baseline).
Bucket reservations (tickets, onboarding...) have no versions: the ones that changed are
replaced and kept for the revert. Nothing here commits; the caller owns the transaction.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.catalogs.models import CatalogItem, CatalogKind, Portfolio
from hourplan.imports import services
from hourplan.imports.hours_sheet import SheetLayout, SheetRow, read_rows
from hourplan.imports.mapping import (
    Mapping,
    clean,
    normalise,
    parse_project_code,
    parse_ticket_codes,
    slug,
)
from hourplan.imports.models import BatchKind, ImportBatch, IssueSeverity
from hourplan.imports.plan_sheets import (
    Row,
    as_date,
    as_hours,
    day_columns,
    find_header,
    header_positions,
    runs,
    sheet_year,
)
from hourplan.planning.models import PlanAllocation, PlanVersion
from hourplan.projects.models import (
    PhaseStatus,
    PhaseTemplate,
    Project,
    ProjectKind,
    ProjectPhase,
)

_STEP_FIELDS = (
    "position",
    "name",
    "phase_type",
    "estimated_by_other_role",
    "requires_approval",
    "blocks_next",
    "counts_as_delivery",
)


@dataclass(frozen=True)
class _Allocation:
    resource_id: int
    phase_id: int | None
    application_id: int | None
    start: date
    end: date
    hours: Decimal

    def fingerprint(self) -> str:
        return "\x1f".join(
            str(v)
            for v in (
                self.resource_id,
                self.phase_id,
                self.application_id,
                self.start,
                self.end,
                self.hours,
            )
        )


@dataclass
class _Plan:
    template: PhaseTemplate | None = None
    phases: dict[int, dict[str, ProjectPhase]] = field(default_factory=dict)
    portfolios: dict[str, Portfolio] = field(default_factory=dict)
    projects: dict[int, list[_Allocation]] = field(default_factory=lambda: defaultdict(list))
    buckets: dict[int, list[_Allocation]] = field(default_factory=lambda: defaultdict(list))
    load: dict[tuple[int, date], Decimal] = field(default_factory=lambda: defaultdict(Decimal))
    reported: set[str] = field(default_factory=set)
    hours_read: Decimal = Decimal(0)
    hours_planned: Decimal = Decimal(0)


def import_plan(
    session: Session, path: Path, *, mapping: Mapping | None = None, actor_id: int | None = None
) -> ImportBatch:
    """Import the backlog and planning sheets of ``path``. Returns the batch."""
    mapping = mapping or Mapping()
    ctx, workbook = services.open_batch(session, path, BatchKind.PLAN, mapping, actor_id)
    plan = _Plan(template=_template(ctx))
    for portfolio in session.scalars(select(Portfolio)):
        plan.portfolios[normalise(portfolio.name)] = portfolio
        plan.portfolios[portfolio.code.upper()] = portfolio
    sheets = {normalise(sheet.title): sheet for sheet in workbook.worksheets}
    try:
        if backlog := sheets.get(mapping.backlog.sheet):
            _import_backlog(ctx, plan, backlog)
        if planning := sheets.get(mapping.plan.sheet):
            _import_planning(ctx, plan, planning, actor_id)
    finally:
        workbook.close()
    if not ctx.stats["sheets"]:
        raise services.ImportRejectedError(
            "El archivo no tiene hojas de backlog ni de planificación reconocibles."
        )
    services.finish_batch(
        ctx,
        actor_id,
        hours_planned_read=str(plan.hours_read),
        hours_planned=str(plan.hours_planned),
    )
    return ctx.batch


def _template(ctx: services._Context) -> PhaseTemplate | None:
    query = select(PhaseTemplate).where(PhaseTemplate.is_active)
    if ctx.mapping.phase_template:
        query = query.where(PhaseTemplate.code == ctx.mapping.phase_template)
    else:
        query = query.order_by(PhaseTemplate.is_default.desc(), PhaseTemplate.id)
    return ctx.session.scalars(query.limit(1)).first()


# ---------------------------------------------------------------- helpers


def _set(ctx: services._Context, table: str, record: Any, column: str, value: object) -> None:
    """Overwrite a column, remembering the first value seen so the revert can put it back."""
    current = getattr(record, column)
    if current == value:
        return
    if record.id not in ctx.created.get(table, []):
        changes = ctx.previous.setdefault(table, {}).setdefault(str(record.id), {})
        changes.setdefault(column, [services.dump(current), None])[1] = services.dump(value)
        ctx.stats[f"{table}_values_changed"] += 1
    setattr(record, column, value)


def _once(
    ctx: services._Context,
    plan: _Plan,
    key: str,
    code: str,
    message: str,
    sheet: str,
    row: int | None,
) -> None:
    if key not in plan.reported:
        plan.reported.add(key)
        ctx.issue(IssueSeverity.WARNING, code, message, sheet, row)


def _phases(ctx: services._Context, plan: _Plan, project: Project) -> dict[str, ProjectPhase]:
    """The project's phases by normalised name, copied from the template if it has none."""
    if project.id in plan.phases:
        return plan.phases[project.id]
    found = list(
        ctx.session.scalars(
            select(ProjectPhase)
            .where(ProjectPhase.project_id == project.id)
            .order_by(ProjectPhase.position)
        )
    )
    if not found and plan.template is not None:
        if project.phase_template_id is None:
            _set(ctx, "project", project, "phase_template_id", plan.template.id)
        for step in plan.template.steps:
            phase = ProjectPhase(
                project_id=project.id,
                template_step_id=step.id,
                **{name: getattr(step, name) for name in _STEP_FIELDS},
            )
            ctx.session.add(phase)
            ctx.track("project_phase", phase)
            ctx.stats["phases_created"] += 1
            found.append(phase)
    plan.phases[project.id] = {normalise(phase.name): phase for phase in found}
    return plan.phases[project.id]


def _portfolio(ctx: services._Context, plan: _Plan, name: str) -> Portfolio | None:
    if not name:
        return None
    portfolio = plan.portfolios.get(normalise(name)) or plan.portfolios.get(slug(name))
    if portfolio is None:
        portfolio = Portfolio(code=slug(name), name=name)
        ctx.session.add(portfolio)
        ctx.track("portfolio", portfolio)
        plan.portfolios[portfolio.code] = portfolio
        ctx.stats["portfolios_created"] += 1
    plan.portfolios[normalise(name)] = portfolio
    return portfolio


def _mapped_item(
    ctx: services._Context, kind: CatalogKind, text: str, codes: dict[str, str]
) -> CatalogItem | None:
    """Catalog item for a cell: the mapped code if it exists, else one named after the text."""
    if not text:
        return None
    code = codes.get(normalise(text))
    if code and (item := ctx.items.get((kind.value, code.upper()))):
        return item
    return services._item(ctx, kind, text)


# ---------------------------------------------------------------- backlog


def _import_backlog(ctx: services._Context, plan: _Plan, sheet: Worksheet) -> None:
    rows = list(sheet.iter_rows(values_only=True))
    config = ctx.mapping.backlog
    found = find_header(rows, config.columns, ("code", "title"))
    if found is None:
        ctx.issue(
            IssueSeverity.INFO, "sheet_skipped", "Hoja sin formato de backlog.", sheet.title, None
        )
        return
    index, fields = found
    positions = header_positions(rows[index])
    ctx.stats["sheets"] += 1
    seen: set[str] = set()
    for number, raw in enumerate(rows[index + 1 :], start=index + 2):

        def cell(name: str, raw: Row = raw) -> object:
            position = fields.get(name)
            return raw[position] if position is not None and position < len(raw) else None

        code_text = clean(cell("code"))
        if not code_text:
            continue
        ctx.stats["backlog_rows"] += 1
        parsed = parse_project_code(code_text, ctx.mapping.project_code)
        if parsed is None or parsed[0] in seen:
            message = (
                f"Código de proyecto no válido: {code_text[:40]!r}; fila no importada."
                if parsed is None
                else f"El código {parsed[0]} se repite en el backlog; se usa la primera fila."
            )
            code = "invalid_project_code" if parsed is None else "duplicate_code"
            ctx.issue(IssueSeverity.WARNING, code, message, sheet.title, number)
            ctx.stats["backlog_rows_skipped"] += 1
            continue
        seen.add(parsed[0])
        title = clean(cell("title"), 200)
        existed = (ProjectKind.PROJECT.value, parsed[0].upper()) in ctx.projects
        project = services._project(
            ctx,
            ProjectKind.PROJECT,
            parsed[0],
            SheetRow(number, {"title": title}),
            sheet.title,
            parsed[1],
        )
        ctx.stats["projects_updated" if existed else "backlog_projects_created"] += 1
        _backlog_project(ctx, plan, project, cell, sheet.title, number)
        _backlog_phases(ctx, plan, project, raw, positions, sheet.title, number)


def _backlog_project(
    ctx: services._Context, plan: _Plan, project: Project, cell: Any, sheet: str, number: int
) -> None:
    config = ctx.mapping.backlog
    if title := clean(cell("title"), 200):
        _set(ctx, "project", project, "title", title)
    if portfolio := _portfolio(ctx, plan, clean(cell("portfolio"), 120)):
        _set(ctx, "project", project, "portfolio_id", portfolio.id)
    status = _mapped_item(
        ctx, CatalogKind.PROJECT_STATUS, clean(cell("status"), 120), config.statuses
    )
    if status:
        _set(ctx, "project", project, "status_id", status.id)
    priority = _mapped_item(
        ctx, CatalogKind.PRIORITY, clean(cell("priority"), 120), config.priorities
    )
    if priority:
        _set(ctx, "project", project, "priority_id", priority.id)
    effort, effort_ok = as_hours(cell("effort"))
    budget, budget_ok = as_hours(cell("budget"))
    if not (effort_ok and budget_ok):
        ctx.issue(
            IssueSeverity.WARNING,
            "bad_effort",
            "Esfuerzo o presupuesto que no es un número de horas; se ignora.",
            sheet,
            number,
        )
    if effort is not None and budget is not None and effort != budget:
        ctx.issue(
            IssueSeverity.INFO,
            "effort_budget_differ",
            f"Esfuerzo {effort} h y presupuesto {budget} h distintos; se usa el esfuerzo.",
            sheet,
            number,
        )
    if (base := effort if effort is not None else budget) is not None:
        _set(ctx, "project", project, "base_effort_hours", base)
    notes = cell("notes")
    if notes is not None and str(notes).strip():
        _set(ctx, "project", project, "notes", str(notes).strip())


def _backlog_phases(
    ctx: services._Context,
    plan: _Plan,
    project: Project,
    raw: Row,
    positions: dict[str, int],
    sheet: str,
    number: int,
) -> None:
    config = ctx.mapping.backlog
    phases = _phases(ctx, plan, project)

    def value(header: str | None) -> object:
        position = positions.get(normalise(header)) if header else None
        return raw[position] if position is not None and position < len(raw) else None

    efforts: dict[str, Decimal] = defaultdict(Decimal)
    for header, step in config.efforts.items():
        hours, ok = as_hours(value(header))
        if not ok:
            ctx.issue(
                IssueSeverity.WARNING,
                "bad_effort",
                f"{header}: no es un número de horas.",
                sheet,
                number,
            )
        elif hours is not None:
            efforts[normalise(step)] += hours

    for spec in config.phases:
        phase = phases.get(normalise(spec.step))
        if phase is None:
            if phases:
                _once(
                    ctx,
                    plan,
                    f"step:{spec.step}",
                    "missing_step",
                    f"La plantilla no tiene la fase {spec.step!r}; se ignoran sus columnas.",
                    sheet,
                    number,
                )
            continue
        status_cell = value(spec.status)
        status_text = normalise(status_cell) if isinstance(status_cell, str) else ""
        if status_cell is not None and not isinstance(status_cell, str):
            ctx.issue(
                IssueSeverity.WARNING,
                "bad_phase_status",
                f"{spec.status}: estado que no es texto.",
                sheet,
                number,
            )
        elif status_text:
            mapped = config.phase_statuses.get(status_text)
            if mapped in set(PhaseStatus):
                _set(ctx, "project_phase", phase, "status", mapped)
            else:
                ctx.issue(
                    IssueSeverity.WARNING,
                    "unknown_phase_status",
                    f"{spec.status}: estado {status_text!r} sin equivalencia.",
                    sheet,
                    number,
                )
        start = _date_cell(ctx, value(spec.start), spec.start, sheet, number)
        end = _date_cell(ctx, value(spec.end), spec.end, sheet, number)
        final_start = start or phase.planned_start
        final_end = end or phase.planned_end
        if final_start and final_end and final_end < final_start:
            ctx.issue(
                IssueSeverity.WARNING,
                "dates_reversed",
                f"{spec.step}: fin {final_end} antes del inicio {final_start}; se ignora el fin.",
                sheet,
                number,
            )
            end = None
            if phase.planned_end and phase.planned_end < final_start:
                _set(ctx, "project_phase", phase, "planned_end", None)
        if start:
            _set(ctx, "project_phase", phase, "planned_start", start)
        if end:
            _set(ctx, "project_phase", phase, "planned_end", end)
        if normalise(spec.step) in efforts:
            _set(ctx, "project_phase", phase, "base_effort_hours", efforts[normalise(spec.step)])


def _date_cell(
    ctx: services._Context, raw: object, header: str | None, sheet: str, number: int
) -> date | None:
    day, ok = as_date(raw)
    if not ok:
        ctx.issue(
            IssueSeverity.WARNING,
            "bad_date",
            f"{header}: fecha no válida {str(raw)[:20]!r}.",
            sheet,
            number,
        )
    return day


# ---------------------------------------------------------------- planning


def _import_planning(
    ctx: services._Context, plan: _Plan, sheet: Worksheet, actor_id: int | None
) -> None:
    rows = list(sheet.iter_rows(values_only=True))
    found = find_header(rows, ctx.mapping.columns, ("resource", "type", "code", "title"))
    if found is None:
        ctx.issue(
            IssueSeverity.INFO,
            "sheet_skipped",
            "Hoja sin formato de planificación.",
            sheet.title,
            None,
        )
        return
    index, fields = found
    year = sheet_year(rows[: index + 1]) or ctx.mapping.plan.year
    if year is None:
        ctx.issue(
            IssueSeverity.ERROR,
            "missing_year",
            "No se sabe el año de las columnas de días; indícalo en plan.year del mapeo.",
            sheet.title,
            None,
        )
        return
    days = day_columns(rows[index], year)
    if not days:
        ctx.issue(
            IssueSeverity.INFO,
            "sheet_skipped",
            "Hoja de planificación sin columnas de días.",
            sheet.title,
            None,
        )
        return
    ctx.stats["sheets"] += 1
    layout = SheetLayout(fields, days)
    for row in read_rows(iter(rows[index + 1 :]), layout, ctx.mapping, first_number=index + 2):
        ctx.stats["plan_rows"] += 1
        _plan_row(ctx, plan, sheet.title, row)
    _store_project_plans(ctx, plan, actor_id)
    _store_reservations(ctx, plan, min(days.values()), max(days.values()))
    _report_load(ctx, plan, sheet.title)


def _plan_row(ctx: services._Context, plan: _Plan, sheet: str, row: SheetRow) -> None:
    for code, message in row.problems:
        ctx.issue(IssueSeverity.WARNING, code, message, sheet, row.number)
    hours = sum((d.hours for d in row.days), Decimal(0))
    plan.hours_read += hours
    if not row.days:
        return
    values = row.values
    name = clean(values.get("resource"), 120)
    if not name:
        ctx.issue(
            IssueSeverity.ERROR,
            "missing_resource",
            "Fila con horas y sin recurso.",
            sheet,
            row.number,
        )
        ctx.stats["plan_rows_skipped"] += 1
        return
    type_text = normalise(values.get("type"))
    kind = ctx.mapping.kinds.get(type_text, "bucket")
    if kind == "absence":
        ctx.issue(
            IssueSeverity.INFO,
            "planned_absence",
            "Ausencia en el plan: regístrala como ausencia; no se importa.",
            sheet,
            row.number,
        )
        ctx.stats["plan_rows_skipped"] += 1
        return
    resource = services._resource(ctx, name, sheet, row.number)
    application = services._application(ctx, clean(values.get("application"), 120))
    project, bucket = _plan_target(ctx, sheet, row, kind)
    phase_id = _plan_phase(ctx, plan, project, values.get("phase", ""), sheet, row.number)
    for day in row.days:
        plan.load[(resource.id, day.day)] += day.hours
    for run in runs(row.days):
        allocation = _Allocation(
            resource.id,
            phase_id,
            application.id if application else None,
            run.start,
            run.end,
            run.hours,
        )
        if project is not None:
            plan.projects[project.id].append(allocation)
        elif bucket is not None:
            plan.buckets[bucket.id].append(allocation)
    plan.hours_planned += hours


def _plan_target(
    ctx: services._Context, sheet: str, row: SheetRow, kind: str
) -> tuple[Project | None, CatalogItem | None]:
    raw_code = row.values.get("code", "")
    if kind == "project":
        parsed = parse_project_code(raw_code, ctx.mapping.project_code)
        if parsed:
            return services._project(
                ctx, ProjectKind.PROJECT, parsed[0], row, sheet, parsed[1]
            ), None
        ctx.issue(
            IssueSeverity.WARNING,
            "invalid_project_code",
            f"Código de proyecto no válido: {clean(raw_code, 40)!r}.",
            sheet,
            row.number,
        )
    elif kind == "ticket":
        ids = parse_ticket_codes(raw_code)
        if ids:
            return services._project(ctx, ProjectKind.TICKET, ids[0], row, sheet, None), None
        return None, services._bucket(ctx, ctx.mapping.plan.ticket_bucket)
    else:
        label = clean(row.values.get("type"), 120)
        if label:
            if (
                not {
                    (CatalogKind.ACTIVITY_BUCKET.value, k) for k in (normalise(label), slug(label))
                }
                & ctx.items.keys()
            ):
                ctx.issue(
                    IssueSeverity.INFO,
                    "new_bucket",
                    f"Tipo {label!r} desconocido: se crea un cubo con ese nombre.",
                    sheet,
                    row.number,
                )
            return None, services._bucket(ctx, label)
        ctx.issue(IssueSeverity.WARNING, "missing_type", "Fila sin tipo.", sheet, row.number)
    return None, services._bucket(ctx, services.UNASSIGNED[1], code=services.UNASSIGNED[0])


def _plan_phase(
    ctx: services._Context,
    plan: _Plan,
    project: Project | None,
    text: str,
    sheet: str,
    number: int,
) -> int | None:
    if project is None or not normalise(text):
        return None
    step = ctx.mapping.plan.phases.get(normalise(text), clean(text))
    if not step:
        return None
    phase = _phases(ctx, plan, project).get(normalise(step))
    if phase is None:
        _once(
            ctx,
            plan,
            f"phase:{project.id}:{normalise(step)}",
            "unknown_phase",
            f"{project.code}: sin fase {step!r}; se planifica sin fase.",
            sheet,
            number,
        )
        return None
    return phase.id


def _keys(ctx: services._Context, prefix: str, allocations: list[_Allocation]) -> list[str]:
    return [services._key(ctx, prefix, allocation.fingerprint()) for allocation in allocations]


def _store_project_plans(ctx: services._Context, plan: _Plan, actor_id: int | None) -> None:
    session = ctx.session
    for project_id, allocations in plan.projects.items():
        keys = _keys(ctx, f"p{project_id}", allocations)
        latest = session.scalars(
            select(PlanVersion)
            .where(PlanVersion.project_id == project_id)
            .order_by(PlanVersion.number.desc())
            .limit(1)
        ).first()
        if latest is not None:
            stored = session.scalars(
                select(PlanAllocation.import_key).where(PlanAllocation.plan_version_id == latest.id)
            )
            if Counter(stored) == Counter(keys):
                ctx.stats["plan_versions_unchanged"] += 1
                continue
        has_baseline = session.scalar(
            select(func.count())
            .select_from(PlanVersion)
            .where(PlanVersion.project_id == project_id, PlanVersion.is_baseline)
        )
        version = PlanVersion(
            project_id=project_id,
            number=(latest.number + 1) if latest else 1,
            label=f"Importado de {ctx.batch.file_name}"[:120],
            is_baseline=not has_baseline,
            created_by_id=actor_id,
            import_batch_id=ctx.batch.id,
        )
        session.add(version)
        session.flush()
        for allocation, key in zip(allocations, keys, strict=True):
            session.add(_row(allocation, key, ctx.batch.id, plan_version_id=version.id))
        ctx.stats["plan_versions_created"] += 1
        ctx.stats["allocations"] += len(allocations)


def _store_reservations(ctx: services._Context, plan: _Plan, first: date, last: date) -> None:
    """Replace the imported reservations of the sheet's period that are no longer in it."""
    session = ctx.session
    wanted: Counter[str] = Counter()
    new: list[tuple[int, _Allocation, str]] = []
    for bucket_id, allocations in plan.buckets.items():
        for allocation, key in zip(
            allocations, _keys(ctx, f"b{bucket_id}", allocations), strict=True
        ):
            wanted[key] += 1
            new.append((bucket_id, allocation, key))
    current = session.scalars(
        select(PlanAllocation).where(
            PlanAllocation.plan_version_id.is_(None),
            PlanAllocation.import_batch_id.is_not(None),
            PlanAllocation.replaced_by_batch_id.is_(None),
            PlanAllocation.start_on <= last,
            PlanAllocation.end_on >= first,
        )
    )
    kept: Counter[str] = Counter()
    for stored in current:
        key = stored.import_key or ""
        if kept[key] < wanted[key]:
            kept[key] += 1
            ctx.stats["reservations_unchanged"] += 1
        else:
            stored.replaced_by_batch_id = ctx.batch.id
            ctx.stats["reservations_replaced"] += 1
    for bucket_id, allocation, key in new:
        if kept[key]:
            kept[key] -= 1
            continue
        session.add(_row(allocation, key, ctx.batch.id, activity_bucket_id=bucket_id))
        ctx.stats["reservations"] += 1


def _row(allocation: _Allocation, key: str, batch_id: int, **target: int) -> PlanAllocation:
    return PlanAllocation(
        resource_id=allocation.resource_id,
        project_phase_id=allocation.phase_id,
        application_id=allocation.application_id,
        start_on=allocation.start,
        end_on=allocation.end,
        hours=allocation.hours,
        import_batch_id=batch_id,
        import_key=key,
        **target,
    )


def _report_load(ctx: services._Context, plan: _Plan, sheet: str) -> None:
    """One finding per resource planned above the workday on some days."""
    limit = ctx.mapping.plan.workday_hours
    names = {resource.id: resource.display_name for resource in ctx.resources.values()}
    over: dict[int, list[tuple[date, Decimal]]] = defaultdict(list)
    for (resource_id, day), hours in plan.load.items():
        if hours > limit:
            over[resource_id].append((day, hours))
    for resource_id, days in sorted(over.items()):
        day, hours = max(days, key=lambda item: (item[1], -item[0].toordinal()))
        ctx.issue(
            IssueSeverity.WARNING,
            "overallocated",
            f"{names[resource_id]}: {len(days)} "
            f"días con más de {limit} h planificadas (máx. {hours} h el {day}).",
            sheet,
            None,
        )
