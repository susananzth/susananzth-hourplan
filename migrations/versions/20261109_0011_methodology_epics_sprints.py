"""methodology epics sprints

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-09 04:03:54.240293
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "epic",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'open'"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_epic_name_not_blank")),
        sa.CheckConstraint("status IN ('open', 'done')", name=op.f("ck_epic_status_valid")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_epic_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_epic")),
        sa.UniqueConstraint("id", "project_id", name=op.f("uq_epic_id")),
    )
    op.create_index(
        "uq_epic_project_name",
        "epic",
        ["project_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.create_table(
        "sprint",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column(
            "status", sa.String(length=10), server_default=sa.text("'planned'"), nullable=False
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_sprint_name_not_blank")),
        sa.CheckConstraint(
            "status IN ('planned', 'active', 'closed')", name=op.f("ck_sprint_status_valid")
        ),
        sa.CheckConstraint("end_date >= start_date", name=op.f("ck_sprint_dates")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_sprint_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sprint")),
        sa.UniqueConstraint("id", "project_id", name=op.f("uq_sprint_id")),
    )
    op.create_index(
        "uq_sprint_one_active",
        "sprint",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_index(
        "uq_sprint_project_name",
        "sprint",
        ["project_id", sa.literal_column("lower(name)")],
        unique=True,
    )
    op.add_column(
        "project",
        sa.Column(
            "methodology",
            sa.String(length=10),
            server_default=sa.text("'waterfall'"),
            nullable=False,
        ),
    )
    op.add_column("project", sa.Column("epic_id", sa.BigInteger(), nullable=True))
    op.add_column("project", sa.Column("sprint_id", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "methodology_valid",
        "project",
        "methodology IN ('waterfall', 'scrum', 'kanban', 'hybrid', 'stories')",
    )
    op.create_check_constraint(
        "agile_needs_parent",
        "project",
        "(epic_id IS NULL AND sprint_id IS NULL) OR parent_id IS NOT NULL",
    )
    op.create_foreign_key(
        "fk_project_epic", "project", "epic", ["epic_id", "parent_id"], ["id", "project_id"]
    )
    op.create_foreign_key(
        "fk_project_sprint", "project", "sprint", ["sprint_id", "parent_id"], ["id", "project_id"]
    )


def downgrade() -> None:
    op.drop_constraint("agile_needs_parent", "project", type_="check")
    op.drop_constraint("methodology_valid", "project", type_="check")
    op.drop_constraint("fk_project_sprint", "project", type_="foreignkey")
    op.drop_constraint("fk_project_epic", "project", type_="foreignkey")
    op.drop_column("project", "sprint_id")
    op.drop_column("project", "epic_id")
    op.drop_column("project", "methodology")
    op.drop_index("uq_sprint_project_name", table_name="sprint")
    op.drop_index(
        "uq_sprint_one_active", table_name="sprint", postgresql_where=sa.text("status = 'active'")
    )
    op.drop_table("sprint")
    op.drop_index("uq_epic_project_name", table_name="epic")
    op.drop_table("epic")
