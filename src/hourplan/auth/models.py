from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hourplan.auth.roles import Role
from hourplan.db import Base, IdMixin, TimestampMixin, enum_check


class UserRole(Base):
    __tablename__ = "user_role"
    __table_args__ = (enum_check("role", Role),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(20), primary_key=True)


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "user_account"
    email: Mapped[str] = mapped_column(String(254))
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    role_links: Mapped[list[UserRole]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", passive_deletes=True
    )

    @property
    def roles(self) -> frozenset[Role]:
        return frozenset(Role(link.role) for link in self.role_links)

    def has_role(self, *roles: Role) -> bool:
        """True if the user holds at least one of ``roles``."""
        return not self.roles.isdisjoint(roles)

    # Flask-Login protocol, implemented here so the model does not import Flask.
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)

    def __repr__(self) -> str:
        return f"<User {self.id}>"


Index("uq_user_account_email_lower", func.lower(User.email), unique=True)
