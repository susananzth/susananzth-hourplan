"""Versioned policies. Once in force a policy is immutable: history is never rewritten."""

from datetime import date
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.db import transaction
from hourplan.policies.models import EvaluationPolicy, VersionedPolicy, WorkPolicy


class PolicyError(ValueError):
    pass


class PolicyInForceError(PolicyError):
    """The policy already applies (or applied) and cannot change."""


class _PolicyData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    effective_from: date


class EvaluationPolicyData(_PolicyData):
    colors_enabled: bool = True
    hours_tolerance_pct: Decimal = Field(ge=0, le=999, decimal_places=2)
    hours_red_pct: Decimal = Field(ge=0, le=999, decimal_places=2)
    dates_tolerance_pct: Decimal = Field(ge=0, le=999, decimal_places=2)
    dates_red_pct: Decimal = Field(ge=0, le=999, decimal_places=2)

    @model_validator(mode="after")
    def _red_above_tolerance(self) -> Self:
        if self.hours_red_pct < self.hours_tolerance_pct:
            raise ValueError("hours_red_pct must be >= hours_tolerance_pct")
        if self.dates_red_pct < self.dates_tolerance_pct:
            raise ValueError("dates_red_pct must be >= dates_tolerance_pct")
        return self


class WorkPolicyData(_PolicyData):
    standard_daily_hours: Decimal = Field(gt=0, le=24, decimal_places=2)
    daily_warning_hours: Decimal = Field(gt=0, le=24, decimal_places=2)
    max_project_weeks: int = Field(gt=0, le=52)
    idle_minutes: int = Field(gt=0, le=240)

    @model_validator(mode="after")
    def _warning_above_standard(self) -> Self:
        if self.daily_warning_hours < self.standard_daily_hours:
            raise ValueError("daily_warning_hours must be >= standard_daily_hours")
        return self


def policy_at[P: VersionedPolicy](session: Session, model: type[P], day: date) -> P | None:
    """The policy of type ``model`` in force on ``day``."""
    stmt = (
        select(model)
        .where(model.effective_from <= day)
        .order_by(model.effective_from.desc())
        .limit(1)
    )
    return session.scalars(stmt).first()


def list_policies[P: VersionedPolicy](session: Session, model: type[P]) -> list[P]:
    """Every version, newest first."""
    return list(session.scalars(select(model).order_by(model.effective_from.desc())))


def schedule_evaluation_policy(
    session: Session, data: EvaluationPolicyData, *, today: date, actor_id: int | None
) -> EvaluationPolicy:
    return _schedule(session, EvaluationPolicy, data, today=today, actor_id=actor_id)


def schedule_work_policy(
    session: Session, data: WorkPolicyData, *, today: date, actor_id: int | None
) -> WorkPolicy:
    return _schedule(session, WorkPolicy, data, today=today, actor_id=actor_id)


def _schedule[P: VersionedPolicy](
    session: Session, model: type[P], data: _PolicyData, *, today: date, actor_id: int | None
) -> P:
    """Create the policy, or replace a future one with the same start date."""
    if data.effective_from < today:
        raise PolicyInForceError("A new policy cannot start in the past")
    values = data.model_dump()
    with transaction(session):
        policy = session.scalars(
            select(model).where(model.effective_from == data.effective_from)
        ).one_or_none()
        if policy is None:
            policy = model(**values)
            policy.created_by_id = actor_id
            session.add(policy)
            session.flush()
            action, changes = AuditAction.CREATE, values
        else:
            if policy.effective_from <= today:
                raise PolicyInForceError("The policy is already in force")
            before = {key: getattr(policy, key) for key in values}
            for key, value in values.items():
                setattr(policy, key, value)
            action, changes = AuditAction.UPDATE, audit.diff(before, values)
        audit.record(
            session,
            action=action,
            entity_type=model.__tablename__,  # type: ignore[attr-defined]
            entity_id=policy.id,
            actor_id=actor_id,
            changes=changes,
        )
    return policy
