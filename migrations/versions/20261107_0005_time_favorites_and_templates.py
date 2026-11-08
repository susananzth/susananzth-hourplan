"""time favorites and templates

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-07 22:16:14.903185
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "time_favorite",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("activity_bucket_id", sa.BigInteger(), nullable=True),
        sa.Column("task_type_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "num_nonnulls(project_id, activity_bucket_id) = 1",
            name=op.f("ck_time_favorite_one_target"),
        ),
        sa.ForeignKeyConstraint(
            ["activity_bucket_id"],
            ["catalog_item.id"],
            name=op.f("fk_time_favorite_activity_bucket_id_catalog_item"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], name=op.f("fk_time_favorite_project_id_project")
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resource.id"],
            name=op.f("fk_time_favorite_resource_id_resource"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_type_id"],
            ["catalog_item.id"],
            name=op.f("fk_time_favorite_task_type_id_catalog_item"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_favorite")),
        sa.UniqueConstraint(
            "resource_id",
            "project_id",
            "activity_bucket_id",
            "task_type_id",
            name=op.f("uq_time_favorite_resource_id"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "time_template",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_time_template_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["resource_id"],
            ["resource.id"],
            name=op.f("fk_time_template_resource_id_resource"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_template")),
        sa.UniqueConstraint("resource_id", "name", name=op.f("uq_time_template_resource_id")),
    )
    op.create_table(
        "time_template_item",
        sa.Column("template_id", sa.BigInteger(), nullable=False),
        sa.Column("project_id", sa.BigInteger(), nullable=True),
        sa.Column("activity_bucket_id", sa.BigInteger(), nullable=True),
        sa.Column("task_type_id", sa.BigInteger(), nullable=True),
        sa.Column("hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint(
            "hours > 0 AND hours <= 24 AND hours * 4 = trunc(hours * 4)",
            name=op.f("ck_time_template_item_hours"),
        ),
        sa.CheckConstraint(
            "num_nonnulls(project_id, activity_bucket_id) = 1",
            name=op.f("ck_time_template_item_one_target"),
        ),
        sa.ForeignKeyConstraint(
            ["activity_bucket_id"],
            ["catalog_item.id"],
            name=op.f("fk_time_template_item_activity_bucket_id_catalog_item"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["project.id"], name=op.f("fk_time_template_item_project_id_project")
        ),
        sa.ForeignKeyConstraint(
            ["task_type_id"],
            ["catalog_item.id"],
            name=op.f("fk_time_template_item_task_type_id_catalog_item"),
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["time_template.id"],
            name=op.f("fk_time_template_item_template_id_time_template"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_time_template_item")),
    )
    op.create_index(
        op.f("ix_time_template_item_template_id"),
        "time_template_item",
        ["template_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_time_template_item_template_id"), table_name="time_template_item")
    op.drop_table("time_template_item")
    op.drop_table("time_template")
    op.drop_table("time_favorite")
