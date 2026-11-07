"""plan import

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05 18:50:56.511585
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "import_batch",
        sa.Column(
            "previous",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.add_column(
        "plan_allocation", sa.Column("activity_bucket_id", sa.BigInteger(), nullable=True)
    )
    op.add_column("plan_allocation", sa.Column("import_batch_id", sa.BigInteger(), nullable=True))
    op.add_column("plan_allocation", sa.Column("import_key", sa.String(length=40), nullable=True))
    op.add_column(
        "plan_allocation", sa.Column("replaced_by_batch_id", sa.BigInteger(), nullable=True)
    )
    op.alter_column("plan_allocation", "plan_version_id", existing_type=sa.BIGINT(), nullable=True)
    op.create_index(
        op.f("ix_plan_allocation_import_batch_id"),
        "plan_allocation",
        ["import_batch_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_plan_allocation_import_batch_id_import_batch"),
        "plan_allocation",
        "import_batch",
        ["import_batch_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_plan_allocation_replaced_by_batch_id_import_batch"),
        "plan_allocation",
        "import_batch",
        ["replaced_by_batch_id"],
        ["id"],
    )
    op.create_foreign_key(
        op.f("fk_plan_allocation_activity_bucket_id_catalog_item"),
        "plan_allocation",
        "catalog_item",
        ["activity_bucket_id"],
        ["id"],
    )
    op.add_column("plan_version", sa.Column("import_batch_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        op.f("fk_plan_version_import_batch_id_import_batch"),
        "plan_version",
        "import_batch",
        ["import_batch_id"],
        ["id"],
    )
    op.add_column("project", sa.Column("notes", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f("ck_plan_allocation_version_or_bucket"),
        "plan_allocation",
        "(plan_version_id IS NULL) <> (activity_bucket_id IS NULL)",
    )
    op.create_check_constraint(
        op.f("ck_plan_allocation_bucket_without_phase"),
        "plan_allocation",
        "activity_bucket_id IS NULL OR project_phase_id IS NULL",
    )
    op.drop_constraint(op.f("ck_import_batch_kind_valid"), "import_batch", type_="check")
    op.create_check_constraint(
        op.f("ck_import_batch_kind_valid"), "import_batch", "kind IN ('hours', 'plan')"
    )


def downgrade() -> None:
    # Bucket reservations cannot exist before this revision; plan batches lose their meaning.
    op.execute("DELETE FROM plan_allocation WHERE plan_version_id IS NULL")
    op.execute("UPDATE plan_allocation SET import_batch_id = NULL, replaced_by_batch_id = NULL")
    op.execute("UPDATE plan_version SET import_batch_id = NULL")
    op.execute("DELETE FROM import_batch WHERE kind = 'plan'")
    op.drop_constraint(op.f("ck_import_batch_kind_valid"), "import_batch", type_="check")
    op.create_check_constraint(
        op.f("ck_import_batch_kind_valid"), "import_batch", "kind IN ('hours')"
    )
    op.drop_constraint(
        op.f("ck_plan_allocation_bucket_without_phase"), "plan_allocation", type_="check"
    )
    op.drop_constraint(
        op.f("ck_plan_allocation_version_or_bucket"), "plan_allocation", type_="check"
    )
    op.drop_column("project", "notes")
    op.drop_constraint(
        op.f("fk_plan_version_import_batch_id_import_batch"), "plan_version", type_="foreignkey"
    )
    op.drop_column("plan_version", "import_batch_id")
    op.drop_constraint(
        op.f("fk_plan_allocation_activity_bucket_id_catalog_item"),
        "plan_allocation",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_plan_allocation_replaced_by_batch_id_import_batch"),
        "plan_allocation",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_plan_allocation_import_batch_id_import_batch"),
        "plan_allocation",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_plan_allocation_import_batch_id"), table_name="plan_allocation")
    op.alter_column("plan_allocation", "plan_version_id", existing_type=sa.BIGINT(), nullable=False)
    op.drop_column("plan_allocation", "replaced_by_batch_id")
    op.drop_column("plan_allocation", "import_key")
    op.drop_column("plan_allocation", "import_batch_id")
    op.drop_column("plan_allocation", "activity_bucket_id")
    op.drop_column("import_batch", "previous")
