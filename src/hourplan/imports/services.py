"""Hours import: reads the workbook, stores one ``time_entry`` per task and day, reports issues.

Importing is idempotent (each row has a fingerprint) and reversible (the batch remembers
what it created). Nothing here commits; the caller owns the transaction.
"""

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.catalogs.models import Application, CatalogItem, CatalogKind, Portfolio
from hourplan.imports.hours_sheet import SheetRow, detect_layout, read_rows
from hourplan.imports.mapping import (
    Mapping,
    clean,
    normalise,
    parse_project_code,
    parse_ticket_codes,
    slug,
)
from hourplan.imports.models import (
    BatchKind,
    BatchStatus,
    ImportBatch,
    ImportIssue,
    IssueSeverity,
)
from hourplan.planning.models import PlanAllocation, PlanVersion
from hourplan.projects.models import Project, ProjectKind, ProjectPhase
from hourplan.resources.models import Resource
from hourplan.timesheet.models import Absence, ApprovalStatus, EntrySource, TimeEntry

UNASSIGNED = ("UNASSIGNED", "Sin código asignado")


class ImportRejectedError(Exception):
    """The file cannot be imported at all (as opposed to a row with a problem)."""


@dataclass
class _Context:
    session: Session
    mapping: Mapping
    batch: ImportBatch
    resources: dict[str, Resource] = field(default_factory=dict)
    projects: dict[tuple[str, str], Project] = field(default_factory=dict)
    applications: dict[str, Application] = field(default_factory=dict)
    items: dict[tuple[str, str], CatalogItem] = field(default_factory=dict)
    known_keys: set[str] = field(default_factory=set)
    occurrences: Counter[str] = field(default_factory=Counter)
    titles: dict[tuple[str, str], str] = field(default_factory=dict)
    created: dict[str, list[int]] = field(default_factory=dict)
    previous: dict[str, dict[str, dict[str, list[Any]]]] = field(default_factory=dict)
    stats: Counter[str] = field(default_factory=Counter)
    hours_read: Decimal = Decimal(0)
    hours_stored: Decimal = Decimal(0)
    hours_absence: Decimal = Decimal(0)

    def issue(
        self, severity: IssueSeverity, code: str, message: str, sheet: str, row: int | None
    ) -> None:
        self.session.add(
            ImportIssue(
                batch_id=self.batch.id,
                severity=severity.value,
                code=code,
                sheet=sheet,
                row_number=row,
                message=message,
            )
        )
        self.stats[f"issues_{severity.value}"] += 1

    def track(self, table: str, record: object) -> None:
        self.session.flush()
        self.created.setdefault(table, []).append(record.id)  # type: ignore[attr-defined]


def import_hours(
    session: Session, path: Path, *, mapping: Mapping | None = None, actor_id: int | None = None
) -> ImportBatch:
    """Import every hours sheet of ``path``. Returns the batch with its issues attached."""
    ctx, workbook = open_batch(session, path, BatchKind.HOURS, mapping or Mapping(), actor_id)
    try:
        for sheet in workbook.worksheets:
            _import_sheet(ctx, sheet)
    finally:
        workbook.close()

    if not ctx.stats["sheets"]:
        raise ImportRejectedError("El archivo no tiene hojas de horas reconocibles.")
    finish_batch(
        ctx,
        actor_id,
        hours_read=str(ctx.hours_read),
        hours_stored=str(ctx.hours_stored),
        hours_absence=str(ctx.hours_absence),
    )
    return ctx.batch


def open_batch(
    session: Session, path: Path, kind: BatchKind, mapping: Mapping, actor_id: int | None
) -> tuple[_Context, Workbook]:
    """Reject a file already imported for ``kind``, open it and start its batch."""
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if session.scalar(
        select(ImportBatch.id).where(
            ImportBatch.kind == kind,
            ImportBatch.file_sha256 == digest,
            ImportBatch.status == BatchStatus.COMPLETED,
        )
    ):
        raise ImportRejectedError("Este archivo ya se importó (mismo contenido).")
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
    except Exception as err:  # openpyxl raises many types for corrupt files
        raise ImportRejectedError(f"No se pudo leer el archivo: {type(err).__name__}") from err

    batch = ImportBatch(
        kind=kind.value, file_name=path.name[:255], file_sha256=digest, started_by_id=actor_id
    )
    session.add(batch)
    session.flush()
    ctx = _Context(session, mapping, batch)
    _preload(ctx)
    return ctx, workbook


def finish_batch(ctx: _Context, actor_id: int | None, **totals: str) -> None:
    """Store what the batch created and changed, its counts, and audit it."""
    batch = ctx.batch
    batch.created = ctx.created
    batch.previous = ctx.previous
    batch.stats = {**ctx.stats, **totals}
    audit.record(
        ctx.session,
        action=AuditAction.CREATE,
        entity_type="import_batch",
        entity_id=batch.id,
        actor_id=actor_id,
        changes={"file": batch.file_name, **batch.stats},
    )
    ctx.session.flush()


def _preload(ctx: _Context) -> None:
    session = ctx.session
    for resource in session.scalars(select(Resource)):
        ctx.resources[normalise(resource.display_name)] = resource
    for project in session.scalars(select(Project)):
        ctx.projects[(project.kind, project.code.upper())] = project
    for application in session.scalars(select(Application)):
        ctx.applications[normalise(application.name)] = application
    for item in session.scalars(select(CatalogItem)):
        ctx.items[(item.kind, normalise(item.name))] = item
        ctx.items[(item.kind, item.code.upper())] = item
    ctx.known_keys.update(
        session.scalars(select(TimeEntry.import_key).where(TimeEntry.import_key.is_not(None)))
    )
    ctx.known_keys.update(
        session.scalars(select(Absence.import_key).where(Absence.import_key.is_not(None)))
    )


def _import_sheet(ctx: _Context, sheet: Worksheet) -> None:
    if sheet.title in ctx.mapping.skip_sheets:
        return
    rows = sheet.iter_rows(values_only=True)
    header = next(rows, None)
    layout = detect_layout(header, ctx.mapping) if header else None
    if layout is None:
        ctx.issue(
            IssueSeverity.INFO, "sheet_skipped", "Hoja sin formato de horas.", sheet.title, None
        )
        return
    ctx.stats["sheets"] += 1
    for row in read_rows(rows, layout, ctx.mapping):
        ctx.stats["rows"] += 1
        _import_row(ctx, sheet.title, row)


def _import_row(ctx: _Context, sheet: str, row: SheetRow) -> None:
    for code, message in row.problems:
        ctx.issue(IssueSeverity.WARNING, code, message, sheet, row.number)
    ctx.hours_read += sum((d.hours for d in row.days), Decimal(0))
    values = row.values
    name = clean(values["resource"], 120)
    if not name:
        ctx.issue(
            IssueSeverity.ERROR,
            "missing_resource",
            "Fila con horas y sin recurso.",
            sheet,
            row.number,
        )
        ctx.stats["rows_skipped"] += 1
        return
    resource = _resource(ctx, name, sheet, row.number)
    type_text = normalise(values["type"])
    kind = ctx.mapping.kinds.get(type_text, "bucket")
    if not type_text:
        ctx.issue(IssueSeverity.WARNING, "missing_type", "Fila sin tipo.", sheet, row.number)

    if kind == "absence":
        _store_absences(ctx, resource, type_text, row)
        return

    target, target_id = _target(ctx, sheet, row, kind, type_text)
    task_type = _item(ctx, CatalogKind.TASK_TYPE, clean(values["task_type"], 120))
    application = _application(ctx, clean(values["application"], 120))
    phase = clean(values["phase"], 60) or None
    description = clean(values["description"]) or None
    for day in row.days:
        fingerprint = "\x1f".join(
            [
                normalise(name), day.day.isoformat(), target, normalise(phase),
                normalise(values["task_type"]), normalise(values["application"]),
                normalise(description), str(day.hours),
            ]
        )  # fmt: skip
        key = _key(ctx, "h", fingerprint)
        if key in ctx.known_keys:
            ctx.stats["entries_skipped_existing"] += 1
            continue
        ctx.known_keys.add(key)
        entry = TimeEntry(
            resource_id=resource.id,
            work_date=day.day,
            hours=day.hours,
            task_type_id=task_type.id if task_type else None,
            application_id=application.id if application else None,
            description=description,
            source=EntrySource.IMPORT.value,
            source_phase=phase,
            import_batch_id=ctx.batch.id,
            import_key=key,
            **target_id,
        )
        ctx.session.add(entry)
        ctx.stats["entries"] += 1
        ctx.hours_stored += day.hours


def _key(ctx: _Context, prefix: str, fingerprint: str) -> str:
    """Stable id of a row; identical rows of one file get consecutive numbers."""
    ctx.occurrences[fingerprint] += 1
    raw = f"{prefix}\x1f{fingerprint}\x1f{ctx.occurrences[fingerprint]}"
    return hashlib.sha1(raw.encode(), usedforsecurity=False).hexdigest()


def _resource(
    ctx: _Context, name: str, sheet: str | None = None, row: int | None = None
) -> Resource:
    key = normalise(name)
    resource = ctx.resources.get(normalise(ctx.mapping.resource_aliases.get(key, key)))
    if resource is None:
        resource = _similar_resource(ctx, key)
        if resource is not None:
            ctx.issue(
                IssueSeverity.INFO, "resource_matched",
                f"{name!r} se asocia al recurso {resource.display_name!r} (mismo nombre "
                "abreviado); si no es la misma persona, usa resource_aliases.", sheet or "", row,
            )  # fmt: skip
    if resource is None:
        resource = Resource(display_name=name)
        ctx.session.add(resource)
        ctx.track("resource", resource)
        ctx.stats["resources_created"] += 1
    ctx.resources[key] = resource
    return resource


def _similar_resource(ctx: _Context, key: str) -> Resource | None:
    """The only resource whose name has all the words of ``key``, or the other way round."""
    words = set(key.split())
    if len(words) < 2:
        return None
    found = {
        resource.id: resource
        for other, resource in ctx.resources.items()
        if len(set(other.split())) >= 2
        and (words <= set(other.split()) or set(other.split()) <= words)
    }
    return next(iter(found.values())) if len(found) == 1 else None


def _target(
    ctx: _Context, sheet: str, row: SheetRow, kind: str, type_text: str
) -> tuple[str, dict[str, int]]:
    """Fingerprint text and the foreign key (project or activity bucket) for a row."""
    raw_code = row.values["code"]
    if kind == "project":
        parsed = parse_project_code(raw_code, ctx.mapping)
        if parsed:
            project = _project(ctx, ProjectKind.PROJECT, parsed[0], row, sheet, parsed[1])
            return f"P:{project.code}", {"project_id": project.id}
        ctx.issue(
            IssueSeverity.WARNING, "invalid_project_code",
            f"Código de proyecto no válido: {clean(raw_code, 40)!r}.", sheet, row.number,
        )  # fmt: skip
    elif kind == "ticket":
        ids = parse_ticket_codes(raw_code)
        if len(ids) > 1:
            ctx.issue(
                IssueSeverity.WARNING, "multiple_tickets",
                f"Varios tickets en una fila ({len(ids)}); se usa el primero.", sheet, row.number,
            )  # fmt: skip
        if ids:
            project = _project(ctx, ProjectKind.TICKET, ids[0], row, sheet, None)
            return f"T:{project.code}", {"project_id": project.id}
        ctx.issue(
            IssueSeverity.WARNING, "invalid_ticket_code",
            f"Ticket sin número: {clean(raw_code, 40)!r}.", sheet, row.number,
        )  # fmt: skip
    else:
        label = clean(row.values["type"], 120)
        if label:
            bucket = _bucket(ctx, label)
            return f"B:{bucket.code}", {"activity_bucket_id": bucket.id}
    bucket = _bucket(ctx, UNASSIGNED[1], code=UNASSIGNED[0])
    return f"B:{bucket.code}", {"activity_bucket_id": bucket.id}


def _project(
    ctx: _Context, kind: ProjectKind, code: str, row: SheetRow, sheet: str, variant: str | None
) -> Project:
    title = clean(row.values["title"], 200)
    project = ctx.projects.get((kind.value, code.upper()))
    if project is None:
        parent_id = None
        if variant:
            parent_code = code.removesuffix(f"-{variant}")
            parent_id = _project(ctx, kind, parent_code, row, sheet, None).id
        project = Project(kind=kind.value, code=code, title=title or code, parent_id=parent_id)
        ctx.session.add(project)
        ctx.track("project", project)
        ctx.projects[(kind.value, code.upper())] = project
        ctx.stats["projects_created"] += 1
        if title:
            ctx.titles[(kind.value, code.upper())] = normalise(title)
    elif title and (kind.value, code.upper()) in ctx.titles:
        if ctx.titles[(kind.value, code.upper())] != normalise(title):
            ctx.titles[(kind.value, code.upper())] = normalise(title)  # report each change once
            ctx.issue(
                IssueSeverity.WARNING, "title_mismatch",
                f"El código {code} aparece con títulos distintos.", sheet, row.number,
            )  # fmt: skip
    return project


def _item(
    ctx: _Context, kind: CatalogKind, name: str, *, code: str | None = None
) -> CatalogItem | None:
    if not name:
        return None
    found = ctx.items.get((kind.value, normalise(name)))
    if found:
        return found
    item = CatalogItem(kind=kind.value, code=code or slug(name), name=name, attributes={})
    existing = ctx.items.get((kind.value, item.code.upper()))
    if existing:  # same code under another spelling: reuse it
        ctx.items[(kind.value, normalise(name))] = existing
        return existing
    ctx.session.add(item)
    ctx.track("catalog_item", item)
    ctx.items[(kind.value, normalise(name))] = item
    ctx.items[(kind.value, item.code.upper())] = item
    ctx.stats["catalog_items_created"] += 1
    return item


def _bucket(ctx: _Context, name: str, *, code: str | None = None) -> CatalogItem:
    item = _item(ctx, CatalogKind.ACTIVITY_BUCKET, name, code=code)
    if item is None:
        raise ValueError("an activity bucket needs a name")
    return item


def _application(ctx: _Context, name: str) -> Application | None:
    if not name:
        return None
    application = ctx.applications.get(normalise(name))
    if application is None:
        application = Application(code=slug(name), name=name)
        ctx.session.add(application)
        ctx.track("application", application)
        ctx.applications[normalise(name)] = application
        ctx.stats["applications_created"] += 1
    return application


def _store_absences(ctx: _Context, resource: Resource, type_text: str, row: SheetRow) -> None:
    absence_type = ctx.mapping.absences.get(type_text, "other")
    for day in row.days:
        key = _key(
            ctx, "a", f"{resource.id}\x1f{day.day.isoformat()}\x1f{absence_type}\x1f{day.hours}"
        )
        if key in ctx.known_keys:
            ctx.stats["absences_skipped_existing"] += 1
            continue
        ctx.known_keys.add(key)
        ctx.session.add(
            Absence(
                resource_id=resource.id,
                absence_type=absence_type,
                start_on=day.day,
                end_on=day.day,
                hours_per_day=day.hours,
                status=ApprovalStatus.APPROVED.value,
                import_batch_id=ctx.batch.id,
                import_key=key,
            )
        )
        ctx.stats["absences"] += 1
        ctx.hours_absence += day.hours


def revert_batch(session: Session, batch: ImportBatch, *, actor_id: int | None = None) -> None:
    """Undo a batch: remove what it stored, restore what it overwrote, and drop the master
    records it created if nothing else uses them."""
    if batch.status == BatchStatus.REVERTED:
        raise ImportRejectedError("El lote ya estaba revertido.")
    removed = {
        "time_entry": _delete(
            session, delete(TimeEntry).where(TimeEntry.import_batch_id == batch.id)
        ),
        "absence": _delete(session, delete(Absence).where(Absence.import_batch_id == batch.id)),
        "plan_allocation": _delete(
            session,
            delete(PlanAllocation).where(
                PlanAllocation.import_batch_id == batch.id, PlanAllocation.plan_version_id.is_(None)
            ),
        ),
        "plan_version": _delete(
            session, delete(PlanVersion).where(PlanVersion.import_batch_id == batch.id)
        ),
    }
    session.execute(
        update(PlanAllocation)
        .where(PlanAllocation.replaced_by_batch_id == batch.id)
        .values(replaced_by_batch_id=None)
    )
    kept = _restore(session, batch.previous)
    # Newest first: a split project goes before its parent.
    for table, model in REVERTIBLE.items():
        for record_id in sorted(batch.created.get(table, []), reverse=True):
            if model is ProjectPhase and session.scalar(
                select(PlanAllocation.id).where(PlanAllocation.project_phase_id == record_id)
            ):  # deleting the phase would cascade to plans stored afterwards
                kept[table] = kept.get(table, 0) + 1
                continue
            try:
                with session.begin_nested():
                    session.execute(delete(model).where(model.id == record_id))
            except IntegrityError:  # still referenced by data entered afterwards
                kept[table] = kept.get(table, 0) + 1
    batch.status = BatchStatus.REVERTED.value
    batch.reverted_at = datetime.now(UTC)
    audit.record(
        session,
        action=AuditAction.UPDATE,
        entity_type="import_batch",
        entity_id=batch.id,
        actor_id=actor_id,
        changes={
            "status": [BatchStatus.COMPLETED.value, batch.status],
            "removed": removed,
            "kept": kept,
        },
    )
    session.flush()


REVERTIBLE: dict[str, Any] = {
    "project_phase": ProjectPhase, "project": Project, "resource": Resource,
    "application": Application, "portfolio": Portfolio, "catalog_item": CatalogItem,
}  # fmt: skip


def _delete(session: Session, statement: Any) -> int:
    result: CursorResult[Any] = session.execute(statement)  # type: ignore[assignment]
    return result.rowcount


def dump(value: object) -> object:
    """JSON form of a column value, for ``ImportBatch.previous``."""
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _restore(session: Session, previous: dict[str, Any]) -> dict[str, int]:
    """Put back overwritten values unless someone changed them after the import."""
    kept: dict[str, int] = {}
    for table, records in previous.items():
        model = REVERTIBLE[table]
        for record_id, columns in records.items():
            record = session.get(model, int(record_id))
            if record is None:
                continue
            for column, (old, new) in columns.items():
                if dump(getattr(record, column)) != new:
                    kept[f"{table}.{column}"] = kept.get(f"{table}.{column}", 0) + 1
                    continue
                python_type = model.__table__.c[column].type.python_type
                if old is not None and python_type is date:
                    old = date.fromisoformat(old)
                elif old is not None and python_type is Decimal:
                    old = Decimal(old)
                setattr(record, column, old)
    session.flush()
    return kept
