from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, IdMixin, TimestampMixin, enum_check


class BatchKind(StrEnum):
    HOURS = "hours"
    PLAN = "plan"


class BatchStatus(StrEnum):
    COMPLETED = "completed"
    REVERTED = "reverted"


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ImportBatch(IdMixin, TimestampMixin, Base):
    """One run of an importer. Everything it created can be reverted as a unit."""

    __tablename__ = "import_batch"
    __table_args__ = (enum_check("kind", BatchKind), enum_check("status", BatchStatus))

    kind: Mapped[str] = mapped_column(String(20))
    file_name: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(10), default=BatchStatus.COMPLETED, server_default=text("'completed'")
    )
    # Row counts and hour totals read from the file and stored.
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # Ids of the master records the batch created, by table, so a revert can remove them.
    created: Mapped[dict[str, list[int]]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    # Values the batch overwrote, by table, id and column: {"project": {"7": {"title": "Old"}}}.
    previous: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    started_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportIssue(IdMixin, Base):
    """A data-quality finding. The importer reports it; it never silently fixes it."""

    __tablename__ = "import_issue"
    __table_args__ = (
        enum_check("severity", IssueSeverity),
        Index(None, "batch_id", "severity"),
    )

    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batch.id", ondelete="CASCADE"))
    severity: Mapped[str] = mapped_column(String(10))
    code: Mapped[str] = mapped_column(String(40))
    sheet: Mapped[str | None] = mapped_column(String(120))
    row_number: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
