"""Initial configuration from a TOML seed. Idempotent: it only creates what is missing."""

import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.catalogs.models import CHILD_KINDS, CatalogItem, CatalogKind, Portfolio
from hourplan.catalogs.services import validate_attributes
from hourplan.db import transaction
from hourplan.policies.models import EvaluationPolicy, Holiday, VersionedPolicy, WorkPolicy
from hourplan.policies.services import EvaluationPolicyData, WorkPolicyData
from hourplan.projects.models import PhaseTemplate, PhaseTemplateStep, PhaseType

DEFAULT_SEED = Path(__file__).with_name("seed.toml")
OVERLAY_SEED_NAME = "seed.toml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CatalogItemSeed(_Strict):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    attributes: dict[str, Any] = Field(default_factory=dict)
    children: list[CatalogItemSeed] = Field(default_factory=list)


class CatalogSeed(_Strict):
    kind: CatalogKind
    items: list[CatalogItemSeed]

    @model_validator(mode="after")
    def _children_allowed(self) -> Self:
        if self.kind not in CHILD_KINDS and any(item.children for item in self.items):
            raise ValueError(f"{self.kind} items cannot have children")
        return self


class StepSeed(_Strict):
    name: str = Field(min_length=1, max_length=120)
    phase_type: PhaseType
    estimated_by_other_role: bool = False
    requires_approval: bool = False
    blocks_next: bool = False
    counts_as_delivery: bool = False


class PhaseTemplateSeed(_Strict):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    is_default: bool = False
    steps: list[StepSeed] = Field(min_length=1)


class NamedSeed(_Strict):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)


class HolidaySeed(_Strict):
    day: date
    name: str = Field(min_length=1, max_length=120)


class Seed(_Strict):
    evaluation_policy: EvaluationPolicyData | None = None
    work_policy: WorkPolicyData | None = None
    catalog: list[CatalogSeed] = Field(default_factory=list)
    phase_template: list[PhaseTemplateSeed] = Field(default_factory=list)
    portfolio: list[NamedSeed] = Field(default_factory=list)
    holiday: list[HolidaySeed] = Field(default_factory=list)

    @model_validator(mode="after")
    def _one_default_template(self) -> Self:
        if sum(template.is_default for template in self.phase_template) > 1:
            raise ValueError("Only one phase template can be the default")
        return self


@dataclass
class SeedReport:
    created: dict[str, int] = field(default_factory=dict)

    def add(self, entity: str) -> None:
        self.created[entity] = self.created.get(entity, 0) + 1


def seed_path(overlay_dir: str | None) -> Path:
    """The overlay seed if the overlay provides one, else the neutral core seed."""
    if overlay_dir:
        candidate = Path(overlay_dir) / OVERLAY_SEED_NAME
        if candidate.is_file():
            return candidate
    return DEFAULT_SEED


def load_seed(path: Path) -> Seed:
    with path.open("rb") as handle:
        return Seed.model_validate(tomllib.load(handle))


def apply_seed(session: Session, seed: Seed) -> SeedReport:
    report = SeedReport()
    with transaction(session):
        if seed.evaluation_policy:
            _seed_policy(session, EvaluationPolicy, seed.evaluation_policy, report)
        if seed.work_policy:
            _seed_policy(session, WorkPolicy, seed.work_policy, report)
        for catalog in seed.catalog:
            _seed_catalog(session, catalog, report)
        _seed_templates(session, seed.phase_template, report)
        _seed_portfolios(session, seed.portfolio, report)
        _seed_holidays(session, seed.holiday, report)
    return report


def _seed_policy(
    session: Session, model: type[VersionedPolicy], data: BaseModel, report: SeedReport
) -> None:
    # Only the first policy comes from the seed; later versions are scheduled by an admin.
    if session.scalars(select(model.id).limit(1)).first() is None:
        session.add(model(**data.model_dump()))
        report.add(model.__tablename__)  # type: ignore[attr-defined]


def _seed_catalog(session: Session, catalog: CatalogSeed, report: SeedReport) -> None:
    existing = _existing_codes(session)
    for order, item in enumerate(catalog.items, start=1):
        parent = _seed_item(session, catalog.kind, item, order, None, existing, report)
        if child_kind := CHILD_KINDS.get(catalog.kind):
            for child_order, child in enumerate(item.children, start=1):
                _seed_item(session, child_kind, child, child_order, parent, existing, report)


def _existing_codes(session: Session) -> dict[tuple[str, str], CatalogItem]:
    return {(item.kind, item.code): item for item in session.scalars(select(CatalogItem))}


def _seed_item(
    session: Session,
    kind: CatalogKind,
    item: CatalogItemSeed,
    order: int,
    parent: CatalogItem | None,
    existing: dict[tuple[str, str], CatalogItem],
    report: SeedReport,
) -> CatalogItem:
    if found := existing.get((kind.value, item.code)):
        return found
    created = CatalogItem(
        kind=kind.value,
        code=item.code,
        name=item.name,
        sort_order=order,
        parent=parent,
        attributes=validate_attributes(kind, item.attributes),
    )
    session.add(created)
    existing[(kind.value, item.code)] = created
    report.add(f"catalog:{kind}")
    return created


def _seed_templates(
    session: Session, templates: list[PhaseTemplateSeed], report: SeedReport
) -> None:
    codes = set(session.scalars(select(PhaseTemplate.code)))
    has_default = session.scalars(select(PhaseTemplate.id).where(PhaseTemplate.is_default)).first()
    for template in templates:
        if template.code in codes:
            continue
        session.add(
            PhaseTemplate(
                code=template.code,
                name=template.name,
                description=template.description,
                is_default=template.is_default and has_default is None,
                steps=[
                    PhaseTemplateStep(position=position, **step.model_dump())
                    for position, step in enumerate(template.steps, start=1)
                ],
            )
        )
        report.add("phase_template")


def _seed_portfolios(session: Session, portfolios: list[NamedSeed], report: SeedReport) -> None:
    codes = set(session.scalars(select(Portfolio.code)))
    for order, portfolio in enumerate(portfolios, start=1):
        if portfolio.code not in codes:
            session.add(Portfolio(code=portfolio.code, name=portfolio.name, sort_order=order))
            report.add("portfolio")


def _seed_holidays(session: Session, holidays: list[HolidaySeed], report: SeedReport) -> None:
    days = set(session.scalars(select(Holiday.holiday_date)))
    for holiday in holidays:
        if holiday.day not in days:
            session.add(Holiday(holiday_date=holiday.day, name=holiday.name))
            report.add("holiday")
