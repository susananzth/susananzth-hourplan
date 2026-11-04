"""Audit trail. Callers record inside their own transaction; nothing here commits."""

from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from hourplan.audit.models import AuditAction, AuditLog


class MissingReasonError(ValueError):
    """A change that requires a justification was attempted without one."""


def record(
    session: Session,
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: int | None,
    actor_id: int | None,
    changes: Mapping[str, Any] | None = None,
    reason: str | None = None,
    reason_required: bool = False,
    subject_user_id: int | None = None,
) -> AuditLog:
    reason = reason.strip() if reason else None
    if reason_required and not reason:
        raise MissingReasonError("A justification is required for this change")
    entry = AuditLog(
        action=action.value,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_id=actor_id,
        changes={key: to_json(value) for key, value in (changes or {}).items()},
        reason=reason,
        subject_user_id=subject_user_id,
    )
    session.add(entry)
    return entry


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, list[Any]]:
    """``{field: [old, new]}`` for the fields whose value changed."""
    return {
        key: [to_json(before.get(key)), to_json(value)]
        for key, value in after.items()
        if before.get(key) != value
    }


def to_json(value: Any) -> Any:
    """Make a value safe for a JSONB column (Decimal and dates as strings)."""
    match value:
        case Enum():
            return value.value
        case Decimal() | date() | datetime():
            return str(value) if isinstance(value, Decimal) else value.isoformat()
        case Mapping():
            return {str(k): to_json(v) for k, v in value.items()}
        case list() | tuple() | set() | frozenset():
            return [to_json(v) for v in value]
        case _:
            return value
