"""import batches

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-04 15:58:04.792293
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_batch",
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status", sa.String(length=10), server_default=sa.text("'completed'"), nullable=False
        ),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("started_by_id", sa.BigInteger(), nullable=True),
        sa.Column("reverted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('hours')", name=op.f("ck_import_batch_kind_valid")),
        sa.CheckConstraint(
            "status IN ('completed', 'reverted')", name=op.f("ck_import_batch_status_valid")
        ),
        sa.ForeignKeyConstraint(
            ["started_by_id"],
            ["user_account.id"],
            name=op.f("fk_import_batch_started_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_batch")),
    )
    op.create_table(
        "import_issue",
        sa.Column("batch_id", sa.BigInteger(), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("sheet", sa.String(length=120), nullable=True),
        sa.Column("row_number", sa.Integer(), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.CheckConstraint(
            "severity IN ('error', 'warning', 'info')", name=op.f("ck_import_issue_severity_valid")
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["import_batch.id"],
            name=op.f("fk_import_issue_batch_id_import_batch"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_issue")),
    )
    op.create_index(
        op.f("ix_import_issue_batch_id"), "import_issue", ["batch_id", "severity"], unique=False
    )
    op.add_column("absence", sa.Column("import_batch_id", sa.BigInteger(), nullable=True))
    op.add_column("absence", sa.Column("import_key", sa.String(length=40), nullable=True))
    op.create_unique_constraint(op.f("uq_absence_import_key"), "absence", ["import_key"])
    op.create_foreign_key(
        op.f("fk_absence_import_batch_id_import_batch"),
        "absence",
        "import_batch",
        ["import_batch_id"],
        ["id"],
    )
    op.add_column("time_entry", sa.Column("source_phase", sa.String(length=60), nullable=True))
    op.add_column("time_entry", sa.Column("import_batch_id", sa.BigInteger(), nullable=True))
    op.add_column("time_entry", sa.Column("import_key", sa.String(length=40), nullable=True))
    op.create_unique_constraint(op.f("uq_time_entry_import_key"), "time_entry", ["import_key"])
    op.create_foreign_key(
        op.f("fk_time_entry_import_batch_id_import_batch"),
        "time_entry",
        "import_batch",
        ["import_batch_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_time_entry_import_batch_id_import_batch"), "time_entry", type_="foreignkey"
    )
    op.drop_constraint(op.f("uq_time_entry_import_key"), "time_entry", type_="unique")
    op.drop_column("time_entry", "import_key")
    op.drop_column("time_entry", "import_batch_id")
    op.drop_column("time_entry", "source_phase")
    op.drop_constraint(
        op.f("fk_absence_import_batch_id_import_batch"), "absence", type_="foreignkey"
    )
    op.drop_constraint(op.f("uq_absence_import_key"), "absence", type_="unique")
    op.drop_column("absence", "import_key")
    op.drop_column("absence", "import_batch_id")
    op.drop_index(op.f("ix_import_issue_batch_id"), table_name="import_issue")
    op.drop_table("import_issue")
    op.drop_table("import_batch")
