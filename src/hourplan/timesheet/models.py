from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    false,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, Hours, IdMixin, TimestampMixin, enum_check

QUARTER_HOURS = "hours * 4 = trunc(hours * 4)"


class EntrySource(StrEnum):
    MANUAL = "manual"
    TIMER = "timer"
    VOICE = "voice"
    IMPORT = "import"
    AI_LOG = "ai_log"


class AbsenceType(StrEnum):
    VACATION = "vacation"
    SICK_LEAVE = "sick_leave"
    PERMISSION = "permission"
    # Day off paid with hours from the time bank.
    COMPENSATION = "compensation"
    OTHER = "other"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class BankReason(StrEnum):
    OVERTIME = "overtime"
    CONTINGENCY = "contingency"
    COMPENSATION = "compensation"
    ADJUSTMENT = "adjustment"


class OvertimeKind(StrEnum):
    VOLUNTARY = "voluntary"
    REQUIRED = "required"


class TimeEntry(IdMixin, TimestampMixin, Base):
    """One row = hours of one resource on one day for one project, ticket or activity bucket.

    Actual hours, monthly totals and the actual Gantt are all queries over this table.
    """

    __tablename__ = "time_entry"
    __table_args__ = (
        CheckConstraint(f"hours > 0 AND hours <= 24 AND {QUARTER_HOURS}", name="hours"),
        CheckConstraint("num_nonnulls(project_id, activity_bucket_id) = 1", name="one_target"),
        CheckConstraint("project_phase_id IS NULL OR project_id IS NOT NULL", name="phase"),
        enum_check("source", EntrySource),
        # The phase must belong to the entry's project.
        ForeignKeyConstraint(
            ["project_phase_id", "project_id"],
            ["project_phase.id", "project_phase.project_id"],
            name="fk_time_entry_phase_in_project",
        ),
        Index(None, "resource_id", "work_date"),
        Index(None, "project_id", "project_phase_id"),
        Index(None, "work_date"),
    )

    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id"))
    work_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[Decimal] = mapped_column(Hours)
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    activity_bucket_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    project_phase_id: Mapped[int | None] = mapped_column(BigInteger)
    task_type_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    application_id: Mapped[int | None] = mapped_column(ForeignKey("application.id"))
    description: Mapped[str | None] = mapped_column(Text)
    # NULL = derived from phase type and cycle dates; a value only corrects an exception.
    is_rework: Mapped[bool | None] = mapped_column(Boolean)
    rework_category_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    ai_assisted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    source: Mapped[str] = mapped_column(
        String(10), default=EntrySource.MANUAL, server_default=text("'manual'")
    )
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Absence(IdMixin, TimestampMixin, Base):
    """Time off. Never counted as hours worked."""

    __tablename__ = "absence"
    __table_args__ = (
        enum_check("absence_type", AbsenceType),
        enum_check("status", ApprovalStatus),
        CheckConstraint("end_on >= start_on", name="dates"),
        CheckConstraint(
            "hours_per_day IS NULL OR (hours_per_day > 0 AND hours_per_day <= 24)",
            name="hours_per_day",
        ),
        Index(None, "resource_id", "start_on"),
    )

    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id"))
    absence_type: Mapped[str] = mapped_column(String(20))
    start_on: Mapped[date] = mapped_column(Date)
    end_on: Mapped[date] = mapped_column(Date)
    # NULL = full working days.
    hours_per_day: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    status: Mapped[str] = mapped_column(
        String(10), default=ApprovalStatus.PENDING, server_default=text("'pending'")
    )
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class TimeBankLedger(IdMixin, TimestampMixin, Base):
    """Signed movements of the time bank: overtime adds, compensation days subtract."""

    __tablename__ = "time_bank_ledger"
    __table_args__ = (
        CheckConstraint(f"hours <> 0 AND {QUARTER_HOURS}", name="hours"),
        enum_check("reason", BankReason),
        enum_check("overtime_kind", OvertimeKind, nullable=True),
        enum_check("status", ApprovalStatus),
        CheckConstraint("reason = 'overtime' OR overtime_kind IS NULL", name="overtime_kind"),
        Index(None, "resource_id", "entry_date"),
    )

    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id"))
    entry_date: Mapped[date] = mapped_column(Date)
    hours: Mapped[Decimal] = mapped_column(Hours)
    reason: Mapped[str] = mapped_column(String(20))
    overtime_kind: Mapped[str | None] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(
        String(10), default=ApprovalStatus.PENDING, server_default=text("'pending'")
    )
    absence_id: Mapped[int | None] = mapped_column(ForeignKey("absence.id", ondelete="SET NULL"))
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
