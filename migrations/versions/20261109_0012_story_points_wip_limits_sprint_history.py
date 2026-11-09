"""story points wip limits sprint history

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-09 04:21:52.336428
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "board_limit",
        sa.Column("project_id", sa.BigInteger(), nullable=False),
        sa.Column("status_id", sa.BigInteger(), nullable=False),
        sa.Column("wip_limit", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("wip_limit >= 1", name=op.f("ck_board_limit_wip_limit")),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_board_limit_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["status_id"],
            ["catalog_item.id"],
            name=op.f("fk_board_limit_status_id_catalog_item"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "status_id", name=op.f("pk_board_limit")),
    )
    op.create_table(
        "sprint_story",
        sa.Column("sprint_id", sa.BigInteger(), nullable=False),
        sa.Column("story_id", sa.BigInteger(), nullable=False),
        sa.Column("story_points", sa.SmallInteger(), nullable=True),
        sa.Column("planned_hours", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("done_on", sa.Date(), nullable=True),
        sa.Column("carried_to_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint("planned_hours >= 0", name=op.f("ck_sprint_story_planned_hours")),
        sa.ForeignKeyConstraint(
            ["carried_to_id"],
            ["sprint.id"],
            name=op.f("fk_sprint_story_carried_to_id_sprint"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["sprint_id"],
            ["sprint.id"],
            name=op.f("fk_sprint_story_sprint_id_sprint"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["story_id"],
            ["project.id"],
            name=op.f("fk_sprint_story_story_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("sprint_id", "story_id", name=op.f("pk_sprint_story")),
    )
    op.add_column("project", sa.Column("story_points", sa.SmallInteger(), nullable=True))
    op.create_check_constraint("story_points", "project", "story_points BETWEEN 0 AND 100")


def downgrade() -> None:
    op.drop_constraint("story_points", "project", type_="check")
    op.drop_column("project", "story_points")
    op.drop_table("sprint_story")
    op.drop_table("board_limit")
