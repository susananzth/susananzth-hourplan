from collections.abc import Callable
from datetime import date

import pytest
from flask.testing import FlaskClient
from pydantic import ValidationError
from sqlalchemy.orm import Session
from werkzeug.test import TestResponse

from hourplan.auth import services as auth
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogItem
from hourplan.projects import cycles, services
from hourplan.projects.models import (
    CycleOutcome,
    PhaseStatus,
    PhaseTemplate,
    PhaseTemplateStep,
    PhaseType,
    Project,
)
from tests.conftest import csrf_token

DAY = date(2026, 7, 6)
Login = Callable[..., TestResponse]
MakeUser = Callable[..., User]


def project(session: Session) -> Project:
    tpl = PhaseTemplate(code="STD", name="Estándar", is_default=True)
    tpl.steps = [
        PhaseTemplateStep(position=1, name="Desarrollo", phase_type=PhaseType.CONSTRUCTION.value),
        PhaseTemplateStep(
            position=2, name="Pruebas", phase_type=PhaseType.VERIFICATION.value, blocks_next=True
        ),
        PhaseTemplateStep(position=3, name="Despliegue", phase_type=PhaseType.DEPLOYMENT.value),
    ]
    session.add(tpl)
    session.commit()
    return services.create_project(
        session, services.ProjectData(code="PJ-00001", title="Portal"), actor_id=None
    )


def origin(session: Session) -> tuple[CatalogItem, CatalogItem, CatalogItem]:
    category = CatalogItem(kind="rework_category", code="EXEC", name="Ejecución")
    other = CatalogItem(kind="rework_category", code="SCOPE", name="Alcance")
    session.add_all([category, other])
    session.flush()
    reason = CatalogItem(
        kind="rework_reason", code="UNTESTED", name="Defecto no probado", parent_id=category.id
    )
    session.add(reason)
    session.commit()
    return category, reason, other


def returned(category_id: int | None, reason_id: int | None = None, obs: str | None = "Falla"):
    return cycles.Decision(
        outcome=CycleOutcome.RETURNED,
        resolved_on=DAY,
        observations=obs,
        rework_category_id=category_id,
        rework_reason_id=reason_id,
    )


def test_only_gated_phases_have_rounds(session: Session) -> None:
    p = project(session)
    with pytest.raises(cycles.CycleError):
        cycles.submit(session, p.phases[0].id, DAY, actor_id=None)


def test_round_trip_returned_then_approved(session: Session) -> None:
    p = project(session)
    category, reason, other = origin(session)
    testing = p.phases[1]

    first = cycles.submit(session, testing.id, DAY, actor_id=None)
    assert testing.status == PhaseStatus.IN_PROGRESS.value
    assert cycles.round_name(testing, first) == "V-1"
    with pytest.raises(cycles.CycleError):
        cycles.submit(session, testing.id, DAY, actor_id=None)
    with pytest.raises(cycles.CycleError):
        cycles.decide(session, first.id, returned(None), decider_id=None)
    with pytest.raises(cycles.CycleError):
        cycles.decide(session, first.id, returned(category.id, obs=None), decider_id=None)
    with pytest.raises(cycles.CycleError):
        cycles.decide(session, first.id, returned(other.id, reason.id), decider_id=None)

    cycles.decide(session, first.id, returned(category.id, reason.id), decider_id=None)
    assert first.outcome == CycleOutcome.RETURNED.value
    assert cycles.returned_count(session, p.id) == 1
    with pytest.raises(cycles.CycleError):
        cycles.submit(session, testing.id, date(2026, 7, 1), actor_id=None)

    second = cycles.submit(session, testing.id, date(2026, 7, 8), actor_id=None)
    assert second.number == 2
    with pytest.raises(cycles.CycleError):
        cycles.decide(
            session,
            second.id,
            cycles.Decision(outcome=CycleOutcome.APPROVED, resolved_on=DAY),
            decider_id=None,
        )
    cycles.decide(
        session,
        second.id,
        cycles.Decision(outcome=CycleOutcome.APPROVED, resolved_on=date(2026, 7, 9)),
        decider_id=None,
    )
    assert testing.status == PhaseStatus.DONE.value
    with pytest.raises(cycles.CycleError):
        cycles.submit(session, testing.id, date(2026, 7, 10), actor_id=None)


def test_pending_is_not_a_decision() -> None:
    with pytest.raises(ValidationError):
        cycles.Decision(outcome=CycleOutcome.PENDING, resolved_on=DAY)


def test_blocking_phase_holds_the_next_ones(session: Session) -> None:
    p = project(session)
    deploy = p.phases[2]

    with pytest.raises(services.ProjectError, match="Pruebas"):
        services.update_phase(
            session, deploy.id, services.PhaseData(status=PhaseStatus.IN_PROGRESS), actor_id=None
        )
    services.update_phase(
        session, p.phases[1].id, services.PhaseData(status=PhaseStatus.SKIPPED), actor_id=None
    )
    services.update_phase(
        session, deploy.id, services.PhaseData(status=PhaseStatus.IN_PROGRESS), actor_id=None
    )
    assert deploy.status == PhaseStatus.IN_PROGRESS.value


def test_rounds_over_the_web(
    client: FlaskClient, session: Session, make_user: MakeUser, login: Login
) -> None:
    p = project(session)
    category, _, _ = origin(session)
    user = make_user()
    auth.grant_role(session, user, Role.LEAD, actor_id=None)
    session.commit()
    login(user.email)
    page = f"/proyectos/{p.id}"
    testing = p.phases[1]

    def post(path: str, form: dict[str, str]) -> TestResponse:
        return client.post(path, data={**form, "csrf_token": csrf_token(client, page)})

    assert "Enviar a pruebas" in client.get(page).get_data(as_text=True)
    post(f"{page}/fases/{testing.id}/rondas", {"fecha": "2026-07-06"})
    session.refresh(testing)
    cycle = testing.cycles[0]
    post(f"{page}/rondas/{cycle.id}", {"fecha": "2026-07-07", "resultado": "returned"})
    assert "escribe las observaciones" in client.get(page).get_data(as_text=True)
    post(
        f"{page}/rondas/{cycle.id}",
        {
            "fecha": "2026-07-07",
            "resultado": "returned",
            "observaciones": "Falta validar montos",
            "categoria": str(category.id),
        },
    )
    html = client.get(page).get_data(as_text=True)
    assert "Observada" in html
    assert "Falta validar montos" in html
    assert "Rondas observadas en el proyecto: 1" in html
