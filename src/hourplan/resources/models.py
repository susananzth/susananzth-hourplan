from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Numeric, String, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, IdMixin, TimestampMixin, not_blank


class Resource(IdMixin, TimestampMixin, Base):
    """A person who can be assigned work. May exist without a user account."""

    __tablename__ = "resource"
    __table_args__ = (
        UniqueConstraint("user_id"),
        not_blank("display_name"),
        CheckConstraint(
            "daily_hours IS NULL OR (daily_hours > 0 AND daily_hours <= 24)", name="daily_hours"
        ),
    )

    display_name: Mapped[str] = mapped_column(String(120))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    # Overrides the standard working day of the work policy.
    daily_hours: Mapped[Decimal | None] = mapped_column(Numeric(4, 2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
