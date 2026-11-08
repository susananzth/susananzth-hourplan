from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.imports import demo
from hourplan.imports.mapping import Mapping
from hourplan.planning.models import PlanAllocation
from hourplan.projects.models import PhaseCycle, Project
from hourplan.reports.models import ComplianceSnapshot
from hourplan.setup import services as setup
from hourplan.timesheet.models import Absence, TimeEntry

TODAY = date(2026, 11, 11)


def test_demo_data_loads_hours_absences_and_plan(session: Session, tmp_path: Path) -> None:
    setup.apply_seed(session, setup.load_seed(setup.DEFAULT_SEED))

    stats = demo.load_demo(session, tmp_path, Mapping(), TODAY)

    assert stats["entries"] != "0"
    assert session.scalar(select(func.count()).select_from(TimeEntry)) > 100
    assert session.scalar(select(func.count()).select_from(Absence)) == 1
    assert session.scalar(select(func.count()).select_from(PlanAllocation)) > 0
    assert session.scalar(select(func.count()).select_from(Project)) >= 3


def test_demo_refuses_a_database_with_hours(session: Session, tmp_path: Path) -> None:
    setup.apply_seed(session, setup.load_seed(setup.DEFAULT_SEED))
    demo.load_demo(session, tmp_path, Mapping(), TODAY)

    with pytest.raises(demo.DemoRefusedError):
        demo.load_demo(session, tmp_path, Mapping(), TODAY)


def test_demo_has_returned_rounds_and_a_closed_project(session: Session, tmp_path: Path) -> None:
    setup.apply_seed(session, setup.load_seed(setup.DEFAULT_SEED))
    demo.load_demo(session, tmp_path, Mapping(), TODAY)

    outcomes = list(session.scalars(select(PhaseCycle.outcome).order_by(PhaseCycle.id)))
    closed = list(session.scalars(select(Project.code).where(Project.closed_on.is_not(None))))
    assert outcomes.count("returned") == 2
    assert "pending" in outcomes
    assert "approved" in outcomes
    assert closed == ["PJ-00003"]
    assert session.scalar(select(func.count()).select_from(ComplianceSnapshot)) == 1
