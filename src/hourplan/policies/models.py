from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, IdMixin, TimestampMixin, not_blank

Percent = Numeric(5, 2)


class VersionedPolicy(IdMixin, TimestampMixin):
    """A policy applies from ``effective_from`` until the next one starts."""

    effective_from: Mapped[date] = mapped_column(Date, unique=True)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )


class EvaluationPolicy(VersionedPolicy, Base):
    """Compliance tolerance: within tolerance = green, up to the red limit = amber, beyond = red."""

    __tablename__ = "evaluation_policy"
    __table_args__ = (
        CheckConstraint(
            "hours_tolerance_pct >= 0 AND hours_red_pct >= hours_tolerance_pct", name="hours"
        ),
        CheckConstraint(
            "dates_tolerance_pct >= 0 AND dates_red_pct >= dates_tolerance_pct", name="dates"
        ),
    )

    colors_enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    hours_tolerance_pct: Mapped[Decimal] = mapped_column(Percent)
    hours_red_pct: Mapped[Decimal] = mapped_column(Percent)
    dates_tolerance_pct: Mapped[Decimal] = mapped_column(Percent)
    dates_red_pct: Mapped[Decimal] = mapped_column(Percent)


class WorkPolicy(VersionedPolicy, Base):
    __tablename__ = "work_policy"
    __table_args__ = (
        CheckConstraint(
            "standard_daily_hours > 0 AND daily_warning_hours >= standard_daily_hours"
            " AND daily_warning_hours <= 24",
            name="daily_hours",
        ),
        CheckConstraint("max_project_weeks > 0", name="max_project_weeks"),
        CheckConstraint("idle_minutes > 0", name="idle_minutes"),
    )

    standard_daily_hours: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal(8), server_default=text("8")
    )
    # Above this many hours in a day the user is warned (never blocked).
    daily_warning_hours: Mapped[Decimal] = mapped_column(
        Numeric(4, 2), default=Decimal(10), server_default=text("10")
    )
    # Longer projects get a non-blocking suggestion to split them.
    max_project_weeks: Mapped[int] = mapped_column(
        SmallInteger, default=3, server_default=text("3")
    )
    idle_minutes: Mapped[int] = mapped_column(SmallInteger, default=5, server_default=text("5"))


class Holiday(IdMixin, Base):
    __tablename__ = "holiday"
    __table_args__ = (UniqueConstraint("holiday_date"), not_blank("name"))

    holiday_date: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(120))
