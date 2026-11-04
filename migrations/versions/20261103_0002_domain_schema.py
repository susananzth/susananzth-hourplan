"""domain schema

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catalog_item",
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(code) <> ''", name=op.f("ck_catalog_item_code_not_blank")),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_catalog_item_name_not_blank")),
        sa.CheckConstraint(
            "kind IN ('requirement_type', 'task_type', 'project_status', 'priority', 'rework_category', 'rework_reason', 'activity_bucket', 'application_type', 'change_request_size')",
            name=op.f("ck_catalog_item_kind_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["catalog_item.id"], name=op.f("fk_catalog_item_parent_id_catalog_item")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catalog_item")),
        sa.UniqueConstraint("kind", "code", name=op.f("uq_catalog_item_kind")),
    )
    op.create_table(
        "holiday",
        sa.Column("holiday_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_holiday_name_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_holiday")),
        sa.UniqueConstraint("holiday_date", name=op.f("uq_holiday_holiday_date")),
    )
    op.create_table(
        "phase_template",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(code) <> ''", name=op.f("ck_phase_template_code_not_blank")),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_phase_template_name_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_phase_template")),
        sa.UniqueConstraint("code", name=op.f("uq_phase_template_code")),
    )
    op.create_index(
        "uq_phase_template_default",
        "phase_template",
        ["is_default"],
        unique=True,
        postgresql_where="is_default",
    )
    op.create_table(
        "portfolio",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(code) <> ''", name=op.f("ck_portfolio_code_not_blank")),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_portfolio_name_not_blank")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portfolio")),
        sa.UniqueConstraint("code", name=op.f("uq_portfolio_code")),
    )
    op.create_table(
        "application",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("type_id", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(code) <> ''", name=op.f("ck_application_code_not_blank")),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_application_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["type_id"], ["catalog_item.id"], name=op.f("fk_application_type_id_catalog_item")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application")),
        sa.UniqueConstraint("code", name=op.f("uq_application_code")),
    )
    op.create_table(
        "audit_log",
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("entity_type", sa.String(length=60), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("subject_user_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "changes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint(
            "action IN ('create', 'update', 'delete')", name=op.f("ck_audit_log_action_valid")
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["user_account.id"],
            name=op.f("fk_audit_log_actor_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["subject_user_id"],
            ["user_account.id"],
            name=op.f("fk_audit_log_subject_user_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        op.f("ix_audit_log_entity_type"), "audit_log", ["entity_type", "entity_id"], unique=False
    )
    op.create_index(op.f("ix_audit_log_occurred_at"), "audit_log", ["occurred_at"], unique=False)
    op.create_index(
        op.f("ix_audit_log_subject_user_id"), "audit_log", ["subject_user_id"], unique=False
    )
    op.create_table(
        "evaluation_policy",
        sa.Column("colors_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("hours_tolerance_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("hours_red_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("dates_tolerance_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("dates_red_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dates_tolerance_pct >= 0 AND dates_red_pct >= dates_tolerance_pct",
            name=op.f("ck_evaluation_policy_dates"),
        ),
        sa.CheckConstraint(
            "hours_tolerance_pct >= 0 AND hours_red_pct >= hours_tolerance_pct",
            name=op.f("ck_evaluation_policy_hours"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["user_account.id"],
            name=op.f("fk_evaluation_policy_created_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evaluation_policy")),
        sa.UniqueConstraint("effective_from", name=op.f("uq_evaluation_policy_effective_from")),
    )
    op.create_table(
        "phase_template_step",
        sa.Column("template_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("phase_type", sa.String(length=20), nullable=False),
        sa.Column(
            "estimated_by_other_role", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "requires_approval", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("blocks_next", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "counts_as_delivery", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_phase_template_step_name_not_blank")),
        sa.CheckConstraint(
            "phase_type IN ('planning', 'construction', 'verification', 'approval', 'deployment')",
            name=op.f("ck_phase_template_step_phase_type_valid"),
        ),
        sa.CheckConstraint("position >= 1", name=op.f("ck_phase_template_step_position")),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["phase_template.id"],
            name=op.f("fk_phase_template_step_template_id_phase_template"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_phase_template_step")),
        sa.UniqueConstraint(
            "template_id",
            "position",
            deferrable=True,
            initially="DEFERRED",
            name=op.f("uq_phase_template_step_template_id"),
        ),
    )
    op.create_table(
        "project",
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("portfolio_id", sa.BigInteger(), nullable=True),
        sa.Column("requirement_type_id", sa.BigInteger(), nullable=True),
        sa.Column("priority_id", sa.BigInteger(), nullable=True),
        sa.Column("status_id", sa.BigInteger(), nullable=True),
        sa.Column("phase_template_id", sa.BigInteger(), nullable=True),
        sa.Column("manager_id", sa.BigInteger(), nullable=True),
        sa.Column("lead_id", sa.BigInteger(), nullable=True),
        sa.Column("base_effort_hours", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("contingency_hours", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("closed_on", sa.Date(), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(code) <> ''", name=op.f("ck_project_code_not_blank")),
        sa.CheckConstraint("btrim(title) <> ''", name=op.f("ck_project_title_not_blank")),
        sa.CheckConstraint("kind IN ('project', 'ticket')", name=op.f("ck_project_kind_valid")),
        sa.CheckConstraint(
            "(base_effort_hours IS NULL OR base_effort_hours >= 0) AND (contingency_hours IS NULL OR contingency_hours >= 0)",
            name=op.f("ck_project_hours"),
        ),
        sa.CheckConstraint("parent_id <> id", name=op.f("ck_project_parent_not_self")),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["user_account.id"],
            name=op.f("fk_project_created_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["lead_id"],
            ["user_account.id"],
            name=op.f("fk_project_lead_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["manager_id"],
            ["user_account.id"],
            name=op.f("fk_project_manager_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["project.id"], name=op.f("fk_project_parent_id_project")
        ),
        sa.ForeignKeyConstraint(
            ["phase_template_id"],
            ["phase_template.id"],
            name=op.f("fk_project_phase_template_id_phase_template"),
        ),
        sa.ForeignKeyConstraint(
            ["portfolio_id"], ["portfolio.id"], name=op.f("fk_project_portfolio_id_portfolio")
        ),
        sa.ForeignKeyConstraint(
            ["priority_id"], ["catalog_item.id"], name=op.f("fk_project_priority_id_catalog_item")
        ),
        sa.ForeignKeyConstraint(
            ["requirement_type_id"],
            ["catalog_item.id"],
            name=op.f("fk_project_requirement_type_id_catalog_item"),
        ),
        sa.ForeignKeyConstraint(
            ["status_id"], ["catalog_item.id"], name=op.f("fk_project_status_id_catalog_item")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project")),
    )
    op.create_index(op.f("ix_project_parent_id"), "project", ["parent_id"], unique=False)
    op.create_index(
        "uq_project_kind_code", "project", ["kind", sa.literal_column("upper(code)")], unique=True
    )
    op.create_table(
        "resource",
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("daily_hours", sa.Numeric(precision=4, scale=2), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(display_name) <> ''", name=op.f("ck_resource_display_name_not_blank")
        ),
        sa.CheckConstraint(
            "daily_hours IS NULL OR (daily_hours > 0 AND daily_hours <= 24)",
            name=op.f("ck_resource_daily_hours"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name=op.f("fk_resource_user_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_resource")),
        sa.UniqueConstraint("user_id", name=op.f("uq_resource_user_id")),
    )
    op.create_table(
        "user_role",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.CheckConstraint(
            "role IN ('admin', 'pm', 'lead', 'developer', 'observer')",
            name=op.f("ck_user_role_role_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name=op.f("fk_user_role_user_id_user_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "role", name=op.f("pk_user_role")),
    )
    op.create_table(
        "work_policy",
        sa.Column(
            "standard_daily_hours",
            sa.Numeric(precision=4, scale=2),
            server_default=sa.text("8"),
            nullable=False,
        ),
        sa.Column(
            "daily_warning_hours",
            sa.Numeric(precision=4, scale=2),
            server_default=sa.text("10"),
            nullable=False,
        ),
        sa.Column(
            "max_project_weeks", sa.SmallInteger(), server_default=sa.text("3"), nullable=False
        ),
        sa.Column("idle_minutes", sa.SmallInteger(), server_default=sa.text("5"), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("idle_minutes > 0", name=op.f("ck_work_policy_idle_minutes")),
        sa.CheckConstraint("max_project_weeks > 0", name=op.f("ck_work_policy_max_project_weeks")),
        sa.CheckConstraint(
            "standard_daily_hours > 0 AND daily_warning_hours >= standard_daily_hours AND daily_warning_hours <= 24",
            name=op.f("ck_work_policy_daily_hours"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["user_account.id"],
            name=op.f("fk_work_policy_created_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_policy")),
        sa.UniqueConstraint("effective_from", name=op.f("uq_work_policy_effective_from")),
    )
    op.create_table(
        "absence",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("absence_type", sa.String(length=20), nullable=False),
        sa.Column("start_on", sa.Date(), nullable=False),
        sa.Column("end_on", sa.Date(), nullable=False),
        sa.Column("hours_per_day", sa.Numeric(precision=4, scale=2), nullable=True),
        sa.Column(
            "status", sa.String(length=10), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "absence_type IN ('vacation', 'sick_leave', 'permission', 'compensation', 'other')",
            name=op.f("ck_absence_absence_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')", name=op.f("ck_absence_status_valid")
        ),
        sa.CheckConstraint("end_on >= start_on", name=op.f("ck_absence_dates")),
        sa.CheckConstraint(
            "hours_per_day IS NULL OR (hours_per_day > 0 AND hours_per_day <= 24)",
            name=op.f("ck_absence_hours_per_day"),
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["user_account.id"],
            name=op.f("fk_absence_decided_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resource.id"], name=op.f("fk_absence_resource_id_resource")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_absence")),
    )
    op.create_index(
        op.f("ix_absence_resource_id"), "absence", ["resource_id", "start_on"], unique=False
    )
    op.create_table(
        "change_request",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("requested_on", sa.Date(), nullable=False),
        sa.Column("size_id", sa.BigInteger(), nullable=True),
        sa.Column("hours_impact", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("approved_by_id", sa.BigInteger(), nullable=True),
        sa.Column("approved_on", sa.Date(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(description) <> ''", name=op.f("ck_change_request_description_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["approved_by_id"],
            ["user_account.id"],
            name=op.f("fk_change_request_approved_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_change_request_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["size_id"], ["catalog_item.id"], name=op.f("fk_change_request_size_id_catalog_item")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_change_request")),
    )
    op.create_index(
        op.f("ix_change_request_project_id"), "change_request", ["project_id"], unique=False
    )
    op.create_table(
        "plan_version",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.SmallInteger(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("is_baseline", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("number >= 1", name=op.f("ck_plan_version_number")),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["user_account.id"],
            name=op.f("fk_plan_version_created_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_plan_version_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_version")),
        sa.UniqueConstraint("project_id", "number", name=op.f("uq_plan_version_project_id")),
    )
    op.create_index(
        "uq_plan_version_baseline",
        "plan_version",
        ["project_id"],
        unique=True,
        postgresql_where="is_baseline",
    )
    op.create_table(
        "project_phase",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("template_step_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status", sa.String(length=20), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("planned_start", sa.Date(), nullable=True),
        sa.Column("planned_end", sa.Date(), nullable=True),
        sa.Column("actual_start", sa.Date(), nullable=True),
        sa.Column("actual_end", sa.Date(), nullable=True),
        sa.Column("base_effort_hours", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("contingency_hours", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("phase_type", sa.String(length=20), nullable=False),
        sa.Column(
            "estimated_by_other_role", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "requires_approval", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("blocks_next", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "counts_as_delivery", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_project_phase_name_not_blank")),
        sa.CheckConstraint(
            "phase_type IN ('planning', 'construction', 'verification', 'approval', 'deployment')",
            name=op.f("ck_project_phase_phase_type_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'in_progress', 'done', 'skipped')",
            name=op.f("ck_project_phase_status_valid"),
        ),
        sa.CheckConstraint(
            "(base_effort_hours IS NULL OR base_effort_hours >= 0) AND (contingency_hours IS NULL OR contingency_hours >= 0)",
            name=op.f("ck_project_phase_hours"),
        ),
        sa.CheckConstraint(
            "actual_end >= actual_start", name=op.f("ck_project_phase_actual_dates")
        ),
        sa.CheckConstraint(
            "planned_end >= planned_start", name=op.f("ck_project_phase_planned_dates")
        ),
        sa.CheckConstraint("position >= 1", name=op.f("ck_project_phase_position")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_project_phase_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_step_id"],
            ["phase_template_step.id"],
            name=op.f("fk_project_phase_template_step_id_phase_template_step"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_phase")),
        sa.UniqueConstraint("id", "project_id", name="uq_project_phase_id_project"),
        sa.UniqueConstraint(
            "project_id",
            "position",
            deferrable=True,
            initially="DEFERRED",
            name=op.f("uq_project_phase_project_id"),
        ),
    )
    op.create_table(
        "repository",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("application_id", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_repository_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["application.id"],
            name=op.f("fk_repository_application_id_application"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repository")),
        sa.UniqueConstraint("name", name=op.f("uq_repository_name")),
    )
    op.create_table(
        "work_item_link",
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("link_type", sa.String(length=20), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "link_type IN ('caused_by', 'relates_to', 'blocks')",
            name=op.f("ck_work_item_link_link_type_valid"),
        ),
        sa.CheckConstraint("source_id <> target_id", name=op.f("ck_work_item_link_not_self")),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["project.id"],
            name=op.f("fk_work_item_link_source_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_id"],
            ["project.id"],
            name=op.f("fk_work_item_link_target_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_work_item_link")),
        sa.UniqueConstraint(
            "source_id", "target_id", "link_type", name=op.f("uq_work_item_link_source_id")
        ),
    )
    op.create_index(
        op.f("ix_work_item_link_target_id"), "work_item_link", ["target_id"], unique=False
    )
    op.create_table(
        "phase_cycle",
        sa.Column("project_phase_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.SmallInteger(), nullable=False),
        sa.Column("submitted_on", sa.Date(), nullable=False),
        sa.Column("resolved_on", sa.Date(), nullable=True),
        sa.Column(
            "outcome", sa.String(length=10), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("observations", sa.Text(), nullable=True),
        sa.Column("rework_category_id", sa.BigInteger(), nullable=True),
        sa.Column("rework_reason_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.Column("is_estimated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome = 'pending' OR resolved_on IS NOT NULL", name=op.f("ck_phase_cycle_resolved")
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'returned', 'approved')",
            name=op.f("ck_phase_cycle_outcome_valid"),
        ),
        sa.CheckConstraint("number >= 1", name=op.f("ck_phase_cycle_number")),
        sa.CheckConstraint("resolved_on >= submitted_on", name=op.f("ck_phase_cycle_dates")),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["user_account.id"],
            name=op.f("fk_phase_cycle_decided_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_phase_id"],
            ["project_phase.id"],
            name=op.f("fk_phase_cycle_project_phase_id_project_phase"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rework_category_id"],
            ["catalog_item.id"],
            name=op.f("fk_phase_cycle_rework_category_id_catalog_item"),
        ),
        sa.ForeignKeyConstraint(
            ["rework_reason_id"],
            ["catalog_item.id"],
            name=op.f("fk_phase_cycle_rework_reason_id_catalog_item"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_phase_cycle")),
        sa.UniqueConstraint(
            "project_phase_id", "number", name=op.f("uq_phase_cycle_project_phase_id")
        ),
    )
    op.create_table(
        "plan_allocation",
        sa.Column("plan_version_id", sa.BigInteger(), nullable=False),
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("project_phase_id", sa.BigInteger(), nullable=True),
        sa.Column("application_id", sa.BigInteger(), nullable=True),
        sa.Column("start_on", sa.Date(), nullable=False),
        sa.Column("end_on", sa.Date(), nullable=False),
        sa.Column("hours", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint("end_on >= start_on", name=op.f("ck_plan_allocation_dates")),
        sa.CheckConstraint("hours >= 0", name=op.f("ck_plan_allocation_hours")),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["application.id"],
            name=op.f("fk_plan_allocation_application_id_application"),
        ),
        sa.ForeignKeyConstraint(
            ["plan_version_id"],
            ["plan_version.id"],
            name=op.f("fk_plan_allocation_plan_version_id_plan_version"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_phase_id"],
            ["project_phase.id"],
            name=op.f("fk_plan_allocation_project_phase_id_project_phase"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resource.id"], name=op.f("fk_plan_allocation_resource_id_resource")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plan_allocation")),
    )
    op.create_index(
        op.f("ix_plan_allocation_plan_version_id"),
        "plan_allocation",
        ["plan_version_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_plan_allocation_resource_id"),
        "plan_allocation",
        ["resource_id", "start_on"],
        unique=False,
    )
    op.create_table(
        "project_repository",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("repository_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_project_repository_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["repository.id"],
            name=op.f("fk_project_repository_repository_id_repository"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "repository_id", name=op.f("pk_project_repository")),
    )
    op.create_table(
        "time_bank_ledger",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("reason", sa.String(length=20), nullable=False),
        sa.Column("overtime_kind", sa.String(length=10), nullable=True),
        sa.Column(
            "status", sa.String(length=10), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("absence_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_by_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "overtime_kind IS NULL OR overtime_kind IN ('voluntary', 'required')",
            name=op.f("ck_time_bank_ledger_overtime_kind_valid"),
        ),
        sa.CheckConstraint(
            "reason = 'overtime' OR overtime_kind IS NULL",
            name=op.f("ck_time_bank_ledger_overtime_kind"),
        ),
        sa.CheckConstraint(
            "reason IN ('overtime', 'contingency', 'compensation', 'adjustment')",
            name=op.f("ck_time_bank_ledger_reason_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'rejected')",
            name=op.f("ck_time_bank_ledger_status_valid"),
        ),
        sa.CheckConstraint(
            "hours <> 0 AND hours * 4 = trunc(hours * 4)", name=op.f("ck_time_bank_ledger_hours")
        ),
        sa.ForeignKeyConstraint(
            ["absence_id"],
            ["absence.id"],
            name=op.f("fk_time_bank_ledger_absence_id_absence"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["user_account.id"],
            name=op.f("fk_time_bank_ledger_created_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["user_account.id"],
            name=op.f("fk_time_bank_ledger_decided_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resource.id"], name=op.f("fk_time_bank_ledger_resource_id_resource")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_bank_ledger")),
    )
    op.create_index(
        op.f("ix_time_bank_ledger_resource_id"),
        "time_bank_ledger",
        ["resource_id", "entry_date"],
        unique=False,
    )
    op.create_table(
        "time_entry",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("activity_bucket_id", sa.BigInteger(), nullable=True),
        sa.Column("project_phase_id", sa.BigInteger(), nullable=True),
        sa.Column("task_type_id", sa.BigInteger(), nullable=True),
        sa.Column("application_id", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_rework", sa.Boolean(), nullable=True),
        sa.Column("rework_category_id", sa.BigInteger(), nullable=True),
        sa.Column("ai_assisted", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "source", sa.String(length=10), server_default=sa.text("'manual'"), nullable=False
        ),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'timer', 'voice', 'import', 'ai_log')",
            name=op.f("ck_time_entry_source_valid"),
        ),
        sa.CheckConstraint(
            "hours > 0 AND hours <= 24 AND hours * 4 = trunc(hours * 4)",
            name=op.f("ck_time_entry_hours"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(project_id, activity_bucket_id) = 1",
            name=op.f("ck_time_entry_one_target"),
        ),
        sa.CheckConstraint(
            "project_phase_id IS NULL OR project_id IS NOT NULL", name=op.f("ck_time_entry_phase")
        ),
        sa.ForeignKeyConstraint(
            ["activity_bucket_id"],
            ["catalog_item.id"],
            name=op.f("fk_time_entry_activity_bucket_id_catalog_item"),
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["application.id"],
            name=op.f("fk_time_entry_application_id_application"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["user_account.id"],
            name=op.f("fk_time_entry_created_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], name=op.f("fk_time_entry_project_id_project")
        ),
        sa.ForeignKeyConstraint(
            ["project_phase_id", "project_id"],
            ["project_phase.id", "project_phase.project_id"],
            name="fk_time_entry_phase_in_project",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resource.id"], name=op.f("fk_time_entry_resource_id_resource")
        ),
        sa.ForeignKeyConstraint(
            ["rework_category_id"],
            ["catalog_item.id"],
            name=op.f("fk_time_entry_rework_category_id_catalog_item"),
        ),
        sa.ForeignKeyConstraint(
            ["task_type_id"],
            ["catalog_item.id"],
            name=op.f("fk_time_entry_task_type_id_catalog_item"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_entry")),
    )
    op.create_index(
        op.f("ix_time_entry_project_id"),
        "time_entry",
        ["project_id", "project_phase_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_time_entry_resource_id"), "time_entry", ["resource_id", "work_date"], unique=False
    )
    op.create_index(op.f("ix_time_entry_work_date"), "time_entry", ["work_date"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_time_entry_work_date"), table_name="time_entry")
    op.drop_index(op.f("ix_time_entry_resource_id"), table_name="time_entry")
    op.drop_index(op.f("ix_time_entry_project_id"), table_name="time_entry")
    op.drop_table("time_entry")
    op.drop_index(op.f("ix_time_bank_ledger_resource_id"), table_name="time_bank_ledger")
    op.drop_table("time_bank_ledger")
    op.drop_table("project_repository")
    op.drop_index(op.f("ix_plan_allocation_resource_id"), table_name="plan_allocation")
    op.drop_index(op.f("ix_plan_allocation_plan_version_id"), table_name="plan_allocation")
    op.drop_table("plan_allocation")
    op.drop_table("phase_cycle")
    op.drop_index(op.f("ix_work_item_link_target_id"), table_name="work_item_link")
    op.drop_table("work_item_link")
    op.drop_table("repository")
    op.drop_table("project_phase")
    op.drop_index(
        "uq_plan_version_baseline", table_name="plan_version", postgresql_where="is_baseline"
    )
    op.drop_table("plan_version")
    op.drop_index(op.f("ix_change_request_project_id"), table_name="change_request")
    op.drop_table("change_request")
    op.drop_index(op.f("ix_absence_resource_id"), table_name="absence")
    op.drop_table("absence")
    op.drop_table("work_policy")
    op.drop_table("user_role")
    op.drop_table("resource")
    op.drop_index("uq_project_kind_code", table_name="project")
    op.drop_index(op.f("ix_project_parent_id"), table_name="project")
    op.drop_table("project")
    op.drop_table("phase_template_step")
    op.drop_table("evaluation_policy")
    op.drop_index(op.f("ix_audit_log_subject_user_id"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_occurred_at"), table_name="audit_log")
    op.drop_index(op.f("ix_audit_log_entity_type"), table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("application")
    op.drop_table("portfolio")
    op.drop_index(
        "uq_phase_template_default", table_name="phase_template", postgresql_where="is_default"
    )
    op.drop_table("phase_template")
    op.drop_table("holiday")
    op.drop_table("catalog_item")
