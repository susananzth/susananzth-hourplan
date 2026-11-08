"""compliance snapshots and deviation notes

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08 16:53:35.137393
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "compliance_snapshot",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("evaluated_on", sa.Date(), nullable=False),
        sa.Column("policy_id", sa.BigInteger(), nullable=True),
        sa.Column("colors_enabled", sa.Boolean(), nullable=False),
        sa.Column("planned_hours", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("actual_hours", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("rework_hours", sa.Numeric(precision=8, scale=2), nullable=False),
        sa.Column("hours_deviation_pct", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("hours_status", sa.String(length=10), nullable=True),
        sa.Column("planned_days", sa.Integer(), nullable=True),
        sa.Column("actual_days", sa.Integer(), nullable=True),
        sa.Column("dates_deviation_pct", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("dates_status", sa.String(length=10), nullable=True),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "dates_status IS NULL OR dates_status IN ('green', 'amber', 'red')",
            name=op.f("ck_compliance_snapshot_dates_status_valid"),
        ),
        sa.CheckConstraint(
            "hours_status IS NULL OR hours_status IN ('green', 'amber', 'red')",
            name=op.f("ck_compliance_snapshot_hours_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["user_account.id"],
            name=op.f("fk_compliance_snapshot_created_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["evaluation_policy.id"],
            name=op.f("fk_compliance_snapshot_policy_id_evaluation_policy"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_compliance_snapshot_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_compliance_snapshot")),
        sa.UniqueConstraint("project_id", name=op.f("uq_compliance_snapshot_project_id")),
    )
    op.create_table(
        "deviation_note",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("author_id", sa.BigInteger(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(body) <> ''", name=op.f("ck_deviation_note_body_not_blank")),
        sa.CheckConstraint("length(body) <= 5000", name=op.f("ck_deviation_note_body_length")),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["user_account.id"],
            name=op.f("fk_deviation_note_author_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_deviation_note_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deviation_note")),
    )
    op.create_index(
        op.f("ix_deviation_note_project_id"), "deviation_note", ["project_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_deviation_note_project_id"), table_name="deviation_note")
    op.drop_table("deviation_note")
    op.drop_table("compliance_snapshot")
