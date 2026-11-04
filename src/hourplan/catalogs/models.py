from enum import StrEnum
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, text, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hourplan.db import Base, IdMixin, TimestampMixin, enum_check, not_blank


class CatalogKind(StrEnum):
    REQUIREMENT_TYPE = "requirement_type"
    TASK_TYPE = "task_type"
    PROJECT_STATUS = "project_status"
    PRIORITY = "priority"
    REWORK_CATEGORY = "rework_category"
    REWORK_REASON = "rework_reason"
    ACTIVITY_BUCKET = "activity_bucket"
    APPLICATION_TYPE = "application_type"
    CHANGE_REQUEST_SIZE = "change_request_size"


# Kind of the children of an item, for the kinds that allow a hierarchy.
CHILD_KINDS: dict[CatalogKind, CatalogKind] = {
    CatalogKind.REWORK_CATEGORY: CatalogKind.REWORK_REASON,
    CatalogKind.ACTIVITY_BUCKET: CatalogKind.ACTIVITY_BUCKET,
}


class CatalogItem(IdMixin, TimestampMixin, Base):
    """Configurable value list. Items are deactivated, never deleted once used."""

    __tablename__ = "catalog_item"
    __table_args__ = (
        UniqueConstraint("kind", "code"),
        enum_check("kind", CatalogKind),
        not_blank("code"),
        not_blank("name"),
    )

    kind: Mapped[str] = mapped_column(String(40))
    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )

    parent: Mapped[CatalogItem | None] = relationship(
        remote_side="CatalogItem.id", back_populates="children"
    )
    children: Mapped[list[CatalogItem]] = relationship(back_populates="parent")

    def __repr__(self) -> str:
        return f"<CatalogItem {self.kind}:{self.code}>"


class Portfolio(IdMixin, TimestampMixin, Base):
    """Portfolio or business unit that groups projects."""

    __tablename__ = "portfolio"
    __table_args__ = (UniqueConstraint("code"), not_blank("code"), not_blank("name"))

    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())


class Application(IdMixin, TimestampMixin, Base):
    """Application, service or database that projects change."""

    __tablename__ = "application"
    __table_args__ = (UniqueConstraint("code"), not_blank("code"), not_blank("name"))

    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    type_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    repositories: Mapped[list[Repository]] = relationship(back_populates="application")


class Repository(IdMixin, TimestampMixin, Base):
    __tablename__ = "repository"
    __table_args__ = (UniqueConstraint("name"), not_blank("name"))

    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(String(500))
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("application.id", ondelete="SET NULL")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    application: Mapped[Application | None] = relationship(back_populates="repositories")
