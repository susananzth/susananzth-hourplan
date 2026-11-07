from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.audit.models import AuditLog
from hourplan.audit.services import MissingReasonError
from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.policies.models import Holiday
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.timesheet import services
from hourplan.timesheet.models import Absence, ApprovalStatus, TimeEntry

MONDAY = date(2026, 6, 15)
TUESDAY = date(2026, 6, 16)


@pytest.fixture
def resource(session: Session) -> Resource:
    item = Resource(display_name="Ana")
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def project(session: Session) -> Project:
    item = Project(code="PROY-0001-2026", title="Portal")
    session.add(item)
    session.commit()
    return item


@pytest.fixture
def bucket(session: Session) -> CatalogItem:
    item = CatalogItem(kind=CatalogKind.ACTIVITY_BUCKET.value, code="meeting", name="Reunión")
    session.add(item)
    session.commit()
    return item


def entry(project: Project, hours: str = "4", day: date = MONDAY) -> services.EntryData:
    return services.EntryData(work_date=day, hours=Decimal(hours), project_id=project.id)


@pytest.mark.parametrize("hours", ["0", "-1", "0.1", "24.25"])
def test_hours_must_be_positive_quarters(project: Project, hours: str) -> None:
    with pytest.raises(ValidationError):
        entry(project, hours)


def test_needs_exactly_one_target(project: Project, bucket: CatalogItem) -> None:
    with pytest.raises(ValidationError):
        services.EntryData(work_date=MONDAY, hours=Decimal(1))
    with pytest.raises(ValidationError):
        services.EntryData(
            work_date=MONDAY, hours=Decimal(1), project_id=project.id, activity_bucket_id=bucket.id
        )


def test_create_warns_past_ten_hours_but_stores(
    session: Session, resource: Resource, project: Project
) -> None:
    _, first = services.create_entry(session, resource.id, entry(project, "8"), actor_id=None)
    assert not first.over_warning
    _, second = services.create_entry(session, resource.id, entry(project, "2.5"), actor_id=None)

    assert second.worked == Decimal("10.5")
    assert second.over_warning
    assert second.overtime == Decimal("2.5")


def test_resource_working_day_overrides_the_default(
    session: Session, resource: Resource, project: Project
) -> None:
    resource.daily_hours = Decimal(6)
    session.commit()
    _, summary = services.create_entry(session, resource.id, entry(project, "8"), actor_id=None)

    assert summary.overtime == Decimal(2)


def test_approved_absence_shortens_the_working_day(
    session: Session, resource: Resource, project: Project
) -> None:
    session.add(
        Absence(
            resource_id=resource.id,
            absence_type="permission",
            start_on=MONDAY,
            end_on=MONDAY,
            hours_per_day=Decimal(3),
            status=ApprovalStatus.APPROVED.value,
        )
    )
    session.commit()
    _, summary = services.create_entry(session, resource.id, entry(project, "6"), actor_id=None)

    assert summary.absence == Decimal(3)
    assert summary.overtime == Decimal(1)


def test_phase_must_belong_to_the_project(
    session: Session, resource: Resource, project: Project
) -> None:
    data = services.EntryData(
        work_date=MONDAY, hours=Decimal(1), project_id=project.id, project_phase_id=999_999
    )
    with pytest.raises(services.EntryError):
        services.create_entry(session, resource.id, data, actor_id=None)


def test_owner_edits_freely_and_leader_needs_a_reason(
    session: Session, resource: Resource, project: Project
) -> None:
    created, _ = services.create_entry(session, resource.id, entry(project, "4"), actor_id=None)
    updated = entry(project, "6")

    services.update_entry(session, created.id, updated, actor_id=None, is_owner=True)
    assert session.get(TimeEntry, created.id).hours == Decimal(6)  # type: ignore[union-attr]

    with pytest.raises(MissingReasonError):
        services.update_entry(
            session, created.id, entry(project, "7"), actor_id=None, is_owner=False
        )
    session.rollback()
    services.update_entry(
        session, created.id, entry(project, "7"), actor_id=None, is_owner=False, reason="Corrección"
    )
    logs = session.scalars(select(AuditLog).where(AuditLog.entity_id == created.id)).all()
    assert [log.reason for log in logs] == [None, "Corrección"]


def test_delete_is_audited(session: Session, resource: Resource, project: Project) -> None:
    created, _ = services.create_entry(session, resource.id, entry(project), actor_id=None)
    entry_id = created.id

    services.delete_entry(session, entry_id, actor_id=None, is_owner=True)

    assert session.get(TimeEntry, entry_id) is None
    assert session.scalars(select(AuditLog).where(AuditLog.entity_id == entry_id)).one()
    with pytest.raises(services.EntryNotFoundError):
        services.delete_entry(session, entry_id, actor_id=None, is_owner=True)


def test_repeat_copies_the_last_day_and_only_once(
    session: Session, resource: Resource, project: Project, bucket: CatalogItem
) -> None:
    services.create_entry(session, resource.id, entry(project, "5"), actor_id=None)
    services.create_entry(
        session,
        resource.id,
        services.EntryData(work_date=MONDAY, hours=Decimal(3), activity_bucket_id=bucket.id),
        actor_id=None,
    )

    copies = services.repeat_day(session, resource.id, TUESDAY, actor_id=None)

    assert sorted(c.hours for c in copies) == [Decimal(3), Decimal(5)]
    assert {c.work_date for c in copies} == {TUESDAY}
    with pytest.raises(services.EntryError):
        services.repeat_day(session, resource.id, TUESDAY, actor_id=None)


def test_repeat_without_a_previous_day_fails(session: Session, resource: Resource) -> None:
    with pytest.raises(services.EntryError):
        services.repeat_day(session, resource.id, TUESDAY, actor_id=None)


def test_working_days_skip_weekends_and_holidays(session: Session) -> None:
    session.add(Holiday(holiday_date=date(2026, 6, 17), name="Feriado"))
    session.commit()

    days = services.working_days(session, MONDAY, date(2026, 6, 21))

    assert days == [MONDAY, TUESDAY, date(2026, 6, 18), date(2026, 6, 19)]
