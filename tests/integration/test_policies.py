from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.audit.models import AuditLog
from hourplan.policies import services
from hourplan.policies.models import EvaluationPolicy, WorkPolicy

TODAY = date(2026, 6, 15)


def evaluation(effective_from: date, tolerance: str = "10") -> services.EvaluationPolicyData:
    return services.EvaluationPolicyData(
        effective_from=effective_from,
        hours_tolerance_pct=Decimal(tolerance),
        hours_red_pct=Decimal(25),
        dates_tolerance_pct=Decimal(10),
        dates_red_pct=Decimal(25),
    )


def test_policy_at_returns_the_one_in_force(session: Session) -> None:
    session.add_all(
        [
            EvaluationPolicy(**evaluation(date(2026, 1, 1)).model_dump()),
            EvaluationPolicy(**evaluation(date(2026, 3, 1), tolerance="5").model_dump()),
        ]
    )
    session.flush()

    assert services.policy_at(session, EvaluationPolicy, date(2025, 12, 31)) is None
    first = services.policy_at(session, EvaluationPolicy, date(2026, 2, 28))
    second = services.policy_at(session, EvaluationPolicy, date(2026, 3, 1))
    assert first is not None
    assert first.hours_tolerance_pct == Decimal(10)
    assert second is not None
    assert second.hours_tolerance_pct == Decimal(5)


def test_new_policy_cannot_start_in_the_past(session: Session) -> None:
    with pytest.raises(services.PolicyInForceError):
        services.schedule_evaluation_policy(
            session, evaluation(date(2026, 6, 14)), today=TODAY, actor_id=None
        )


def test_future_policy_can_be_replaced_and_is_audited(session: Session) -> None:
    start = date(2026, 7, 1)
    created = services.schedule_evaluation_policy(
        session, evaluation(start), today=TODAY, actor_id=None
    )
    updated = services.schedule_evaluation_policy(
        session, evaluation(start, tolerance="5"), today=TODAY, actor_id=None
    )

    assert updated.id == created.id
    assert updated.hours_tolerance_pct == Decimal(5)
    rows = session.scalars(
        select(AuditLog).where(AuditLog.entity_type == "evaluation_policy").order_by(AuditLog.id)
    ).all()
    assert [row.action for row in rows] == ["create", "update"]
    assert rows[1].changes == {"hours_tolerance_pct": ["10", "5"]}


def test_policy_in_force_is_immutable(session: Session) -> None:
    services.schedule_work_policy(
        session,
        services.WorkPolicyData(
            effective_from=TODAY,
            standard_daily_hours=Decimal(8),
            daily_warning_hours=Decimal(10),
            max_project_weeks=3,
            idle_minutes=5,
        ),
        today=TODAY,
        actor_id=None,
    )

    with pytest.raises(services.PolicyInForceError):
        services.schedule_work_policy(
            session,
            services.WorkPolicyData(
                effective_from=TODAY,
                standard_daily_hours=Decimal(7),
                daily_warning_hours=Decimal(10),
                max_project_weeks=3,
                idle_minutes=5,
            ),
            today=TODAY,
            actor_id=None,
        )
    policy = services.policy_at(session, WorkPolicy, TODAY)
    assert policy is not None
    assert policy.standard_daily_hours == Decimal(8)


def test_policy_data_rejects_inconsistent_thresholds() -> None:
    with pytest.raises(ValidationError, match="Límite rojo de horas"):
        evaluation(TODAY, tolerance="30")
    with pytest.raises(ValidationError, match="Aviso diario"):
        services.WorkPolicyData(
            effective_from=TODAY,
            standard_daily_hours=Decimal(8),
            daily_warning_hours=Decimal(6),
            max_project_weeks=3,
            idle_minutes=5,
        )
