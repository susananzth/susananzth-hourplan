"""mcp tokens and ai drafts

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-08 22:55:56.389946
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "access_token",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=12), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(name) <> ''", name=op.f("ck_access_token_name_not_blank")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user_account.id"],
            name=op.f("fk_access_token_user_id_user_account"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_access_token")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_access_token_token_hash")),
    )
    op.create_index(op.f("ix_access_token_user_id"), "access_token", ["user_id"], unique=False)
    op.create_table(
        "ai_draft",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("token_id", sa.BigInteger(), nullable=True),
        sa.Column("work_date", sa.Date(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("hours", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status", sa.String(length=10), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'discarded')", name=op.f("ck_ai_draft_status_valid")
        ),
        sa.CheckConstraint(
            "hours > 0 AND hours <= 24 AND hours * 4 = trunc(hours * 4)",
            name=op.f("ck_ai_draft_hours"),
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resource.id"], name=op.f("fk_ai_draft_resource_id_resource")
        ),
        sa.ForeignKeyConstraint(
            ["token_id"],
            ["access_token.id"],
            name=op.f("fk_ai_draft_token_id_access_token"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_draft")),
    )
    op.create_index(
        op.f("ix_ai_draft_resource_id"), "ai_draft", ["resource_id", "status"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_draft_resource_id"), table_name="ai_draft")
    op.drop_table("ai_draft")
    op.drop_index(op.f("ix_access_token_user_id"), table_name="access_token")
    op.drop_table("access_token")
