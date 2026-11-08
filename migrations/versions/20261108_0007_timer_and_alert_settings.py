"""timer and alert settings

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-08 19:23:47.735206
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alert_fired",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("percent", sa.SmallInteger(), nullable=False),
        sa.Column(
            "fired_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_alert_fired_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resource.id"],
            name=op.f("fk_alert_fired_resource_id_resource"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_fired")),
        sa.UniqueConstraint(
            "project_id", "resource_id", "percent", name=op.f("uq_alert_fired_project_id")
        ),
    )
    op.create_table(
        "alert_setting",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=True),
        sa.Column("thresholds", postgresql.ARRAY(sa.SmallInteger()), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_alert_setting_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resource.id"],
            name=op.f("fk_alert_setting_resource_id_resource"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_setting")),
        sa.UniqueConstraint(
            "project_id",
            "resource_id",
            name=op.f("uq_alert_setting_project_id"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "timer_preference",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("idle_minutes", sa.SmallInteger(), server_default="5", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "idle_minutes BETWEEN 1 AND 120", name=op.f("ck_timer_preference_idle_minutes")
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resource.id"],
            name=op.f("fk_timer_preference_resource_id_resource"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timer_preference")),
        sa.UniqueConstraint("resource_id", name=op.f("uq_timer_preference_resource_id")),
    )
    op.create_table(
        "timer_session",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("segment_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_beat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accumulated_seconds", sa.Integer(), server_default="0", nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("activity_bucket_id", sa.BigInteger(), nullable=True),
        sa.Column("project_phase_id", sa.BigInteger(), nullable=True),
        sa.Column("task_type_id", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ai_assisted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_interruption", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("interrupted_id", sa.BigInteger(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(status = 'running') = (segment_started_at IS NOT NULL)",
            name=op.f("ck_timer_session_segment"),
        ),
        sa.CheckConstraint(
            "status IN ('running', 'paused', 'stopped', 'discarded')",
            name=op.f("ck_timer_session_status_valid"),
        ),
        sa.CheckConstraint("accumulated_seconds >= 0", name=op.f("ck_timer_session_accumulated")),
        sa.CheckConstraint(
            "num_nonnulls(project_id, activity_bucket_id) <= 1",
            name=op.f("ck_timer_session_one_target"),
        ),
        sa.CheckConstraint(
            "project_phase_id IS NULL OR project_id IS NOT NULL",
            name=op.f("ck_timer_session_phase"),
        ),
        sa.ForeignKeyConstraint(
            ["activity_bucket_id"],
            ["catalog_item.id"],
            name=op.f("fk_timer_session_activity_bucket_id_catalog_item"),
        ),
        sa.ForeignKeyConstraint(
            ["interrupted_id"],
            ["timer_session.id"],
            name=op.f("fk_timer_session_interrupted_id_timer_session"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], name=op.f("fk_timer_session_project_id_project")
        ),
        sa.ForeignKeyConstraint(
            ["project_phase_id", "project_id"],
            ["project_phase.id", "project_phase.project_id"],
            name="fk_timer_session_phase_in_project",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resource.id"], name=op.f("fk_timer_session_resource_id_resource")
        ),
        sa.ForeignKeyConstraint(
            ["task_type_id"],
            ["catalog_item.id"],
            name=op.f("fk_timer_session_task_type_id_catalog_item"),
        ),
        sa.ForeignKeyConstraint(
            ["time_entry_id"],
            ["time_entry.id"],
            name=op.f("fk_timer_session_time_entry_id_time_entry"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timer_session")),
    )
    op.create_index(
        op.f("ix_timer_session_resource_id"),
        "timer_session",
        ["resource_id", "status"],
        unique=False,
    )
    op.create_index(
        "uq_timer_session_one_running",
        "timer_session",
        ["resource_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_timer_session_one_running",
        table_name="timer_session",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_index(op.f("ix_timer_session_resource_id"), table_name="timer_session")
    op.drop_table("timer_session")
    op.drop_table("timer_preference")
    op.drop_table("alert_setting")
    op.drop_table("alert_fired")
