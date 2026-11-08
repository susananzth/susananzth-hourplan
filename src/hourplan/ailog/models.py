"""Personal access tokens for the MCP endpoint and the hour drafts an AI client proposes."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, Hours, IdMixin, TimestampMixin, enum_check, not_blank


class AccessToken(IdMixin, TimestampMixin, Base):
    """Only the SHA-256 of the token is stored; the token itself is shown once."""

    __tablename__ = "access_token"
    __table_args__ = (not_blank("name"), Index(None, "user_id"))

    user_id: Mapped[int] = mapped_column(ForeignKey("user_account.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(60))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(12))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DraftStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class AiDraft(IdMixin, TimestampMixin, Base):
    """One line proposed through MCP. It becomes hours only when the person confirms it."""

    __tablename__ = "ai_draft"
    __table_args__ = (
        enum_check("status", DraftStatus),
        CheckConstraint("hours > 0 AND hours <= 24 AND hours * 4 = trunc(hours * 4)", name="hours"),
        Index(None, "resource_id", "status"),
    )

    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id"))
    token_id: Mapped[int | None] = mapped_column(ForeignKey("access_token.id", ondelete="SET NULL"))
    work_date: Mapped[date] = mapped_column(Date)
    code: Mapped[str] = mapped_column(String(40))
    hours: Mapped[Decimal] = mapped_column(Hours)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(10), default=DraftStatus.PENDING, server_default=text("'pending'")
    )
