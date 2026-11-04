from datetime import datetime

from sqlalchemy import Boolean, DateTime, Index, String, func, true
from sqlalchemy.orm import Mapped, mapped_column

from hourplan.db import Base, IdMixin, TimestampMixin


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "user_account"
    email: Mapped[str] = mapped_column(String(254))
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
