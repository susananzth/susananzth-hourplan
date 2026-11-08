"""voice dictation

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08 19:58:13.927672
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "voice_dictation",
        sa.Column("resource_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "status", sa.String(length=10), server_default=sa.text("'queued'"), nullable=False
        ),
        sa.Column("audio", sa.LargeBinary(), nullable=True),
        sa.Column("duration_s", sa.Numeric(precision=7, scale=2), nullable=True),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("draft", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("attempts", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("stt_model", sa.String(length=80), nullable=True),
        sa.Column("llm_model", sa.String(length=80), nullable=True),
        sa.Column("stt_seconds", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("llm_seconds", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed', 'confirmed', 'discarded')",
            name=op.f("ck_voice_dictation_status_valid"),
        ),
        sa.CheckConstraint("attempts >= 0", name=op.f("ck_voice_dictation_attempts")),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["user_account.id"],
            name=op.f("fk_voice_dictation_created_by_id_user_account"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id"], ["resource.id"], name=op.f("fk_voice_dictation_resource_id_resource")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_dictation")),
    )
    op.create_index(
        op.f("ix_voice_dictation_resource_id"),
        "voice_dictation",
        ["resource_id", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voice_dictation_status"), "voice_dictation", ["status", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_voice_dictation_status"), table_name="voice_dictation")
    op.drop_index(op.f("ix_voice_dictation_resource_id"), table_name="voice_dictation")
    op.drop_table("voice_dictation")
