from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, IdMixin, enum_check


class AuditAction(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class AuditLog(IdMixin, Base):
    """Append-only record of changes to relevant entities."""

    __tablename__ = "audit_log"
    __table_args__ = (
        enum_check("action", AuditAction),
        Index(None, "entity_type", "entity_id"),
        Index(None, "subject_user_id"),
        Index(None, "occurred_at"),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(10))
    entity_type: Mapped[str] = mapped_column(String(60))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    # User whose data was changed (e.g. the owner of an edited time entry): they can see it.
    subject_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    changes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    reason: Mapped[str | None] = mapped_column(Text)
