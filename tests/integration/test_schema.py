"""Database rules that protect the domain even if a service has a bug."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.models import metadata
from hourplan.planning.models import PlanVersion
from hourplan.projects.models import (
    CycleOutcome,
    PhaseCycle,
    PhaseType,
    Project,
    ProjectKind,
    ProjectPhase,
)
from hourplan.resources.models import Resource
from hourplan.timesheet.models import TimeEntry


def test_migrations_match_models(engine: Engine) -> None:
    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True})
        assert compare_metadata(context, metadata) == []


@pytest.fixture
def violates(session: Session) -> Callable[[Any], None]:
    """Assert that flushing ``obj`` breaks a database constraint, keeping the test usable."""

    def _check(obj: Any) -> None:
        savepoint = session.begin_nested()
        session.add(obj)
        with pytest.raises(IntegrityError):
            session.flush()
        savepoint.rollback()

    return _check


@pytest.fixture
def project(session: Session) -> Project:
    project = Project(
        code="PJ-00001",
        title="Proyecto",
        phases=[
            ProjectPhase(position=1, name="Desarrollo", phase_type=PhaseType.CONSTRUCTION),
            ProjectPhase(position=2, name="Pruebas", phase_type=PhaseType.VERIFICATION),
        ],
    )
    session.add(project)
    session.flush()
    return project


@pytest.fixture
def resource(session: Session) -> Resource:
    resource = Resource(display_name="Persona")
    session.add(resource)
    session.flush()
    return resource


def entry(resource: Resource, **values: Any) -> TimeEntry:
    return TimeEntry(resource_id=resource.id, work_date=date(2026, 1, 5), **values)


@pytest.mark.parametrize("hours", ["0", "0.30", "24.25", "-1"])
def test_time_entry_hours_are_positive_quarters(
    violates: Callable[[Any], None], resource: Resource, project: Project, hours: str
) -> None:
    violates(entry(resource, hours=Decimal(hours), project_id=project.id))


def test_time_entry_accepts_quarter_hours(
    session: Session, resource: Resource, project: Project
) -> None:
    session.add(entry(resource, hours=Decimal("0.75"), project_id=project.id))
    session.flush()


def test_time_entry_has_exactly_one_target(
    session: Session, violates: Callable[[Any], None], resource: Resource, project: Project
) -> None:
    bucket = CatalogItem(kind=CatalogKind.ACTIVITY_BUCKET, code="MEETING", name="Reunión")
    session.add(bucket)
    session.flush()

    violates(entry(resource, hours=Decimal(1)))
    violates(entry(resource, hours=Decimal(1), project_id=project.id, activity_bucket_id=bucket.id))


def test_time_entry_phase_must_belong_to_its_project(
    session: Session, violates: Callable[[Any], None], resource: Resource, project: Project
) -> None:
    other = Project(code="PJ-00002", title="Otro")
    session.add(other)
    session.flush()

    violates(
        entry(
            resource, hours=Decimal(1), project_id=other.id, project_phase_id=project.phases[0].id
        )
    )


def test_project_code_is_unique_per_kind_ignoring_case(
    session: Session, violates: Callable[[Any], None], project: Project
) -> None:
    violates(Project(code="pj-00001", title="Duplicado"))

    session.add(Project(kind=ProjectKind.TICKET, code="PJ-00001", title="Ticket"))
    session.flush()


def test_enum_columns_reject_unknown_values(
    violates: Callable[[Any], None], project: Project
) -> None:
    violates(ProjectPhase(project_id=project.id, position=3, name="X", phase_type="other"))


def test_only_one_baseline_per_project(
    session: Session, violates: Callable[[Any], None], project: Project
) -> None:
    session.add(PlanVersion(project_id=project.id, number=1, is_baseline=True))
    session.flush()

    violates(PlanVersion(project_id=project.id, number=2, is_baseline=True))


def test_resolved_cycle_needs_a_resolution_date(
    violates: Callable[[Any], None], project: Project
) -> None:
    violates(
        PhaseCycle(
            project_phase_id=project.phases[1].id,
            number=1,
            submitted_on=date(2026, 1, 5),
            outcome=CycleOutcome.RETURNED,
        )
    )


def test_phase_positions_can_be_swapped_in_one_transaction(
    session: Session, project: Project
) -> None:
    first, second = project.phases
    first.position, second.position = 2, 1
    session.flush()
    session.commit()
