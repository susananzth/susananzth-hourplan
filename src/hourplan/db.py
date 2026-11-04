"""SQLAlchemy declarative base, shared mixins and the transaction helper."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import BigInteger, CheckConstraint, DateTime, Identity, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# Hours worked or planned: numeric, never float.
Hours = Numeric(6, 2)
LongHours = Numeric(8, 2)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Primary keys are bigint, so foreign keys must be too; small ints are declared explicitly.
    type_annotation_map: ClassVar[dict[Any, Any]] = {int: BigInteger}


class IdMixin:
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=lambda: datetime.now(UTC)
    )


def enum_check(column: str, enum: type[StrEnum], *, nullable: bool = False) -> CheckConstraint:
    """CHECK constraint that limits ``column`` to the values of ``enum``."""
    values = ", ".join(f"'{member.value}'" for member in enum)
    condition = f"{column} IN ({values})"
    if nullable:
        condition = f"{column} IS NULL OR {condition}"
    return CheckConstraint(condition, name=f"{column}_valid")


def not_blank(column: str) -> CheckConstraint:
    return CheckConstraint(f"btrim({column}) <> ''", name=f"{column}_not_blank")


@contextmanager
def transaction(session: Session) -> Iterator[Session]:
    """Commit on success and roll back on error, whether or not a transaction already began."""
    if not session.in_transaction():
        with session.begin():
            yield session
        return
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
