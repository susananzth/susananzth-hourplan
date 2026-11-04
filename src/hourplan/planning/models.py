from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hourplan.db import Base, IdMixin, LongHours, TimestampMixin


class PlanVersion(IdMixin, TimestampMixin, Base):
    """A version of a project's plan. The baseline is kept; replanning adds versions."""

    __tablename__ = "plan_version"
    __table_args__ = (
        UniqueConstraint("project_id", "number"),
        CheckConstraint("number >= 1", name="number"),
        Index(
            "uq_plan_version_baseline",
            "project_id",
            unique=True,
            postgresql_where="is_baseline",
        ),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(SmallInteger)
    label: Mapped[str | None] = mapped_column(String(120))
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    reason: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )

    allocations: Mapped[list[PlanAllocation]] = relationship(
        back_populates="plan_version", cascade="all, delete-orphan", passive_deletes=True
    )


class PlanAllocation(IdMixin, Base):
    """Hours of one resource on one phase (and optionally one application) between two dates."""

    __tablename__ = "plan_allocation"
    __table_args__ = (
        CheckConstraint("end_on >= start_on", name="dates"),
        CheckConstraint("hours >= 0", name="hours"),
        Index(None, "plan_version_id"),
        Index(None, "resource_id", "start_on"),
    )

    plan_version_id: Mapped[int] = mapped_column(ForeignKey("plan_version.id", ondelete="CASCADE"))
    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id"))
    project_phase_id: Mapped[int | None] = mapped_column(
        ForeignKey("project_phase.id", ondelete="CASCADE")
    )
    application_id: Mapped[int | None] = mapped_column(ForeignKey("application.id"))
    start_on: Mapped[date] = mapped_column(Date)
    end_on: Mapped[date] = mapped_column(Date)
    hours: Mapped[Decimal] = mapped_column(LongHours)

    plan_version: Mapped[PlanVersion] = relationship(back_populates="allocations")
