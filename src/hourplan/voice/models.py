from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, IdMixin, TimestampMixin, enum_check


class DictationStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"


class VoiceDictation(IdMixin, TimestampMixin, Base):
    """One dictation: the audio lives here only until it is transcribed, then it is erased."""

    __tablename__ = "voice_dictation"
    __table_args__ = (
        enum_check("status", DictationStatus),
        CheckConstraint("attempts >= 0", name="attempts"),
        Index(None, "status", "created_at"),
        Index(None, "resource_id", "created_at"),
    )

    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id"))
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(10), default=DictationStatus.QUEUED, server_default=text("'queued'")
    )
    audio: Mapped[bytes | None] = mapped_column(LargeBinary)
    duration_s: Mapped[float | None] = mapped_column(Numeric(7, 2))
    transcript: Mapped[str | None] = mapped_column(Text)
    draft: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(300))
    attempts: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))
    stt_model: Mapped[str | None] = mapped_column(String(80))
    llm_model: Mapped[str | None] = mapped_column(String(80))
    stt_seconds: Mapped[float | None] = mapped_column(Numeric(8, 2))
    llm_seconds: Mapped[float | None] = mapped_column(Numeric(8, 2))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
