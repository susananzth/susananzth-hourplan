from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, IdMixin, TimestampMixin, enum_check


class TimerStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    DISCARDED = "discarded"


OPEN_STATUSES = (TimerStatus.RUNNING.value, TimerStatus.PAUSED.value)


class TimerSession(IdMixin, TimestampMixin, Base):
    """A stopwatch whose start lives on the server, so closing the browser loses nothing.

    ``accumulated_seconds`` holds the closed segments; a running timer adds the time since
    ``segment_started_at``, never past the last heartbeat once the heartbeats stop.
    """

    __tablename__ = "timer_session"
    __table_args__ = (
        enum_check("status", TimerStatus),
        CheckConstraint("accumulated_seconds >= 0", name="accumulated"),
        CheckConstraint("num_nonnulls(project_id, activity_bucket_id) <= 1", name="one_target"),
        CheckConstraint("project_phase_id IS NULL OR project_id IS NOT NULL", name="phase"),
        CheckConstraint("(status = 'running') = (segment_started_at IS NOT NULL)", name="segment"),
        ForeignKeyConstraint(
            ["project_phase_id", "project_id"],
            ["project_phase.id", "project_phase.project_id"],
            name="fk_timer_session_phase_in_project",
        ),
        # Single focus: at most one running timer per person.
        Index(
            "uq_timer_session_one_running",
            "resource_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
        ),
        Index(None, "resource_id", "status"),
    )

    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id"))
    status: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    segment_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accumulated_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    activity_bucket_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    project_phase_id: Mapped[int | None] = mapped_column()
    task_type_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    description: Mapped[str | None] = mapped_column(Text)
    ai_assisted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # A timer opened with the Interruption button; it is classified when it ends.
    is_interruption: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    interrupted_id: Mapped[int | None] = mapped_column(
        ForeignKey("timer_session.id", ondelete="SET NULL")
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Seconds of the earlier tasks of an uninterrupted run ("Cambiar tarea"): the bar's clock
    # keeps counting from there; the hours of each task are still saved separately.
    chain_seconds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    time_entry_id: Mapped[int | None] = mapped_column(ForeignKey("time_entry.id"))


class TimerPreference(IdMixin, TimestampMixin, Base):
    __tablename__ = "timer_preference"
    __table_args__ = (
        UniqueConstraint("resource_id"),
        CheckConstraint("idle_minutes BETWEEN 1 AND 120", name="idle_minutes"),
    )

    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id", ondelete="CASCADE"))
    idle_minutes: Mapped[int] = mapped_column(SmallInteger, default=5, server_default="5")


class AlertSetting(IdMixin, TimestampMixin, Base):
    """Consumed-budget thresholds (%) of a project.

    ``resource_id`` NULL is the project template; a row with a resource replaces the template for
    that person. An empty list means that person wants no alerts for the project.
    """

    __tablename__ = "alert_setting"
    __table_args__ = (
        UniqueConstraint("project_id", "resource_id", postgresql_nulls_not_distinct=True),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    resource_id: Mapped[int | None] = mapped_column(ForeignKey("resource.id", ondelete="CASCADE"))
    thresholds: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger))


class AlertFired(IdMixin, Base):
    """An alert already delivered, so each threshold fires once per person and project."""

    __tablename__ = "alert_fired"
    __table_args__ = (UniqueConstraint("project_id", "resource_id", "percent"),)

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    resource_id: Mapped[int] = mapped_column(ForeignKey("resource.id", ondelete="CASCADE"))
    percent: Mapped[int] = mapped_column(SmallInteger)
    fired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
