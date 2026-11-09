"""Hours by person, application, task type and month, with rework and contingency absorbed.

Rows are sorted by name or date, never by hours: this is a statistic, not a ranking.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from hourplan.catalogs.models import Application, CatalogItem
from hourplan.reports import rework
from hourplan.reports.models import Compliance, ComplianceSnapshot
from hourplan.resources.models import Resource
from hourplan.shared.translate import N_, _
from hourplan.timesheet.models import TimeEntry

ZERO = Decimal(0)
DIMENSIONS = {
    "persona": N_("Persona"),
    "aplicacion": N_("Aplicación"),
    "tipo": N_("Tipo de tarea"),
    "mes": N_("Mes"),
}
NONE_LABEL = {"aplicacion": N_("Sin aplicación"), "tipo": N_("Sin tipo de tarea")}


@dataclass
class Row:
    label: str
    hours: Decimal
    rework: Decimal
    contingency: Decimal
    initial_pct: Decimal = ZERO
    rework_pct: Decimal = ZERO

    @property
    def initial(self) -> Decimal:
        return self.hours - self.rework

    @property
    def rework_share(self) -> Decimal | None:
        return (self.rework / self.hours * 100).quantize(Decimal("0.1")) if self.hours else None


def _scale(rows: list[Row]) -> list[Row]:
    biggest = max((r.hours for r in rows), default=ZERO)
    for row in rows:
        if biggest:
            row.initial_pct = (row.initial / biggest * 100).quantize(Decimal("0.1"))
            row.rework_pct = (row.rework / biggest * 100).quantize(Decimal("0.1"))
    return rows


def _key(dim: str, task_type: Any) -> tuple[Any, Any]:
    """(group key, sortable label) for a dimension."""
    match dim:
        case "persona":
            return Resource.display_name, Resource.display_name
        case "aplicacion":
            return Application.name, func.coalesce(Application.name, "")
        case "tipo":
            return task_type.name, func.coalesce(task_type.name, "")
        case _:
            month = func.to_char(TimeEntry.work_date, "YYYY-MM")
            return month, month


def hours_by(
    session: Session,
    dim: str,
    start: date,
    end: date,
    *,
    resource_id: int | None = None,
) -> list[Row]:
    if dim not in DIMENSIONS:
        raise ValueError(dim)
    sc = rework.scope()
    task_type = aliased(CatalogItem)
    bucket = aliased(CatalogItem)
    key, order = _key(dim, task_type)
    contingency = func.coalesce(
        func.sum(
            case(
                (bucket.attributes["is_contingency"].as_boolean().is_(True), TimeEntry.hours),
                else_=0,
            )
        ),
        0,
    )
    stmt = (
        sc.attach(select(key, sc.total_hours(), sc.rework_hours(), contingency))
        .join(Resource, Resource.id == TimeEntry.resource_id)
        .outerjoin(Application, Application.id == TimeEntry.application_id)
        .outerjoin(task_type, task_type.id == TimeEntry.task_type_id)
        .outerjoin(bucket, bucket.id == TimeEntry.activity_bucket_id)
        .where(TimeEntry.work_date >= start, TimeEntry.work_date <= end)
        .group_by(key)
        .order_by(order)
    )
    if resource_id:
        stmt = stmt.where(TimeEntry.resource_id == resource_id)
    fallback = _(NONE_LABEL.get(dim, "—"))
    return _scale(
        [
            Row(label or fallback, Decimal(h), Decimal(r), Decimal(c))
            for label, h, r, c in session.execute(stmt)
        ]
    )


@dataclass(frozen=True)
class Totals:
    hours: Decimal
    rework: Decimal
    contingency: Decimal
    closed: dict[str, int]

    @property
    def rework_share(self) -> Decimal | None:
        return (self.rework / self.hours * 100).quantize(Decimal("0.1")) if self.hours else None


def totals(session: Session, start: date, end: date, *, resource_id: int | None = None) -> Totals:
    rows = hours_by(session, "mes", start, end, resource_id=resource_id)
    snapshots = session.scalars(
        select(ComplianceSnapshot.hours_status).where(
            ComplianceSnapshot.evaluated_on >= start, ComplianceSnapshot.evaluated_on <= end
        )
    )
    closed = dict.fromkeys((c.value for c in Compliance), 0)
    closed["none"] = 0
    for status in snapshots:
        closed[status or "none"] += 1
    return Totals(
        sum((r.hours for r in rows), ZERO),
        sum((r.rework for r in rows), ZERO),
        sum((r.contingency for r in rows), ZERO),
        closed,
    )
