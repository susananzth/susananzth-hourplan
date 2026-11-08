from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, IdMixin, LongHours, TimestampMixin, enum_check, not_blank

Deviation = Numeric(8, 2)


class Compliance(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


class ComplianceSnapshot(IdMixin, TimestampMixin, Base):
    """Compliance of a closed project, frozen with the policy in force on its closing date.

    Changing the tolerance later never rewrites it; a recalculation is a separate report.
    """

    __tablename__ = "compliance_snapshot"
    __table_args__ = (
        UniqueConstraint("project_id"),
        enum_check("hours_status", Compliance, nullable=True),
        enum_check("dates_status", Compliance, nullable=True),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    evaluated_on: Mapped[date] = mapped_column(Date)
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("evaluation_policy.id", ondelete="SET NULL")
    )
    colors_enabled: Mapped[bool] = mapped_column(Boolean)
    planned_hours: Mapped[Decimal | None] = mapped_column(LongHours)
    actual_hours: Mapped[Decimal] = mapped_column(LongHours)
    rework_hours: Mapped[Decimal] = mapped_column(LongHours)
    hours_deviation_pct: Mapped[Decimal | None] = mapped_column(Deviation)
    hours_status: Mapped[str | None] = mapped_column(String(10))
    planned_days: Mapped[int | None] = mapped_column(Integer)
    actual_days: Mapped[int | None] = mapped_column(Integer)
    dates_deviation_pct: Mapped[Decimal | None] = mapped_column(Deviation)
    dates_status: Mapped[str | None] = mapped_column(String(10))
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class DeviationNote(IdMixin, TimestampMixin, Base):
    """Explanation of a deviation, written by a lead or manager. Never edited or deleted."""

    __tablename__ = "deviation_note"
    __table_args__ = (
        not_blank("body"),
        CheckConstraint("length(body) <= 5000", name="body_length"),
        Index(None, "project_id"),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    body: Mapped[str] = mapped_column(Text)
