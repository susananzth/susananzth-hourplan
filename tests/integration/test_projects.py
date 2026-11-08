from collections.abc import Callable
from datetime import date
from decimal import Decimal

import pytest
from flask.testing import FlaskClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.audit.models import AuditLog
from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.policies import services as policies
from hourplan.projects import services
from hourplan.projects.models import (
    PhaseStatus,
    PhaseTemplate,
    PhaseTemplateStep,
    PhaseType,
    Project,
)
from tests.conftest import csrf_token

TODAY = date(2026, 7, 6)
Login = Callable[..., TestResponse]
MakeUser = Callable[..., User]


def template(session: Session, default: bool = True) -> PhaseTemplate:
    tpl = PhaseTemplate(code="STD", name="Estándar", is_default=default)
    tpl.steps = [
        PhaseTemplateStep(position=1, name="Análisis", phase_type=PhaseType.PLANNING.value),
        PhaseTemplateStep(position=2, name="Construcción", phase_type=PhaseType.CONSTRUCTION.value),
        PhaseTemplateStep(
            position=3, name="Pruebas", phase_type=PhaseType.VERIFICATION.value, blocks_next=True
        ),
    ]
    session.add(tpl)
    session.commit()
    return tpl


def data(code: str = "PJ-00001", **extra: object) -> services.ProjectData:
    return services.ProjectData(code=code, title="Portal de ejemplo", **extra)  # type: ignore[arg-type]


def test_create_copies_default_template_phases(session: Session) -> None:
    template(session)

    project = services.create_project(session, data(), actor_id=None)

    assert [p.name for p in project.phases] == ["Análisis", "Construcción", "Pruebas"]
    assert project.phases[2].blocks_next is True
    assert project.phase_template_id is not None
    assert session.scalar(select(AuditLog.id).where(AuditLog.entity_type == "project"))


def test_code_is_unique_ignoring_case(session: Session) -> None:
    template(session)
    services.create_project(session, data("PJ-00001"), actor_id=None)

    with pytest.raises(services.DuplicateCodeError):
        services.create_project(session, data("pj-00001"), actor_id=None)


def test_hours_must_be_quarters_and_non_negative() -> None:
    with pytest.raises(ValidationError):
        data(base_effort_hours=Decimal("10.1"))
    with pytest.raises(ValidationError):
        data(contingency_hours=Decimal(-1))


def test_update_project_keeps_template_and_audits(session: Session) -> None:
    template(session)
    project = services.create_project(session, data(), actor_id=None)
    phases = len(project.phases)

    services.update_project(
        session,
        project.id,
        data("PJ-00002", base_effort_hours=Decimal(40)),
        actor_id=None,
    )

    session.refresh(project)
    assert project.code == "PJ-00002"
    assert project.base_effort_hours == Decimal(40)
    assert len(project.phases) == phases
    last = session.scalars(select(AuditLog).order_by(AuditLog.id.desc())).first()
    assert last is not None
    assert last.action == "update"
    assert "code" in last.changes


def test_subproject_cannot_be_its_own_parent(session: Session) -> None:
    template(session)
    project = services.create_project(session, data(), actor_id=None)

    with pytest.raises(services.ProjectError):
        services.update_project(session, project.id, data(parent_id=project.id), actor_id=None)


def test_phase_dates_and_duration_warning(session: Session) -> None:
    template(session)
    policies.schedule_work_policy(
        session,
        policies.WorkPolicyData(
            effective_from=TODAY,
            standard_daily_hours=Decimal(8),
            daily_warning_hours=Decimal(10),
            max_project_weeks=3,
            idle_minutes=5,
        ),
        today=TODAY,
        actor_id=None,
    )
    project = services.create_project(session, data(), actor_id=None)
    first, last = project.phases[0], project.phases[-1]

    services.update_phase(
        session,
        first.id,
        services.PhaseData(
            status=PhaseStatus.IN_PROGRESS,
            planned_start=date(2026, 7, 6),
            planned_end=date(2026, 7, 10),
        ),
        actor_id=None,
    )
    assert services.duration_warning(session, project, TODAY) is None

    services.update_phase(
        session,
        last.id,
        services.PhaseData(
            status=PhaseStatus.PENDING,
            planned_start=date(2026, 8, 3),
            planned_end=date(2026, 8, 14),
        ),
        actor_id=None,
    )
    warning = services.duration_warning(session, project, TODAY)
    assert warning is not None
    assert "subproyectos" in warning


def test_phase_data_rules() -> None:
    with pytest.raises(ValidationError):
        services.PhaseData(status=PhaseStatus.PENDING, planned_start=TODAY)
    with pytest.raises(ValidationError):
        services.PhaseData(
            status=PhaseStatus.PENDING, planned_start=TODAY, planned_end=date(2026, 7, 1)
        )


def sign_in(session: Session, make_user: MakeUser, login: Login, *roles: Role) -> User:
    user = make_user()
    for role in roles:
        auth.grant_role(session, user, role, actor_id=None)
    session.commit()
    assert login(user.email).status_code == 302
    return user


def post(client: FlaskClient, path: str, form: dict[str, str], page: str = "/proyectos/nuevo"):
    return client.post(path, data={**form, "csrf_token": csrf_token(client, page)})


def test_pm_creates_and_edits_phase_over_the_web(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    template(session)
    sign_in(session, make_user, login, Role.PM)

    response = post(client, "/proyectos/nuevo", {"codigo": "PJ-00001", "titulo": "Portal"})
    assert response.status_code == 302
    project = session.scalars(select(Project)).one()
    html = client.get(response.headers["Location"]).get_data(as_text=True)
    assert "PJ-00001" in html
    assert "Construcción" in html

    phase = project.phases[0]
    edit = post(
        client,
        f"/proyectos/{project.id}/fases/{phase.id}",
        {"estado": "in_progress", "inicio": "2026-07-06", "fin": "2026-07-03", "esfuerzo": "8"},
        page=f"/proyectos/{project.id}",
    )
    assert edit.status_code == 302
    assert "anterior" in client.get(f"/proyectos/{project.id}").get_data(as_text=True)
    ok = post(
        client,
        f"/proyectos/{project.id}/fases/{phase.id}",
        {"estado": "in_progress", "inicio": "2026-07-06", "fin": "2026-07-10", "esfuerzo": "8"},
        page=f"/proyectos/{project.id}",
    )
    assert ok.status_code == 302
    session.refresh(phase)
    assert phase.planned_end == date(2026, 7, 10)
    assert phase.base_effort_hours == Decimal(8)


def test_error_shows_label_not_field_name(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    template(session)
    sign_in(session, make_user, login, Role.PM)

    response = post(
        client, "/proyectos/nuevo", {"codigo": "PJ-1", "titulo": "X", "esfuerzo": "abc"}
    )

    html = response.get_data(as_text=True)
    assert "Esfuerzo base (h)" in html
    assert "base_effort_hours" not in html


def test_observer_reads_but_cannot_create(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    template(session)
    project = services.create_project(session, data(), actor_id=None)
    sign_in(session, make_user, login, Role.OBSERVER)

    assert "PJ-00001" in client.get("/proyectos").get_data(as_text=True)
    assert client.get(f"/proyectos/{project.id}").status_code == 200
    assert client.get("/proyectos/nuevo").status_code == 403
    assert client.get("/proyectos/9999").status_code == 404
