"""timer chain seconds

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-08 21:50:17.518180
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "timer_session",
        sa.Column("chain_seconds", sa.Integer(), server_default="0", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("timer_session", "chain_seconds")
