"""Catalog rules shared by every feature that references a catalog item."""

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.catalogs.models import (
    CHILD_KINDS,
    Application,
    CatalogItem,
    CatalogKind,
    Portfolio,
    Repository,
)
from hourplan.db import Base, transaction


class CatalogError(ValueError):
    pass


class DuplicateError(CatalogError):
    """Another record already uses this code or name."""


class _Attributes(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _StatusAttributes(_Attributes):
    is_closed: bool = False


class _ReworkCategoryAttributes(_Attributes):
    attributable_to_developer: bool = True


class _ActivityBucketAttributes(_Attributes):
    is_contingency: bool = False


ATTRIBUTE_SCHEMAS: dict[CatalogKind, type[_Attributes]] = {
    CatalogKind.PROJECT_STATUS: _StatusAttributes,
    CatalogKind.REWORK_CATEGORY: _ReworkCategoryAttributes,
    CatalogKind.ACTIVITY_BUCKET: _ActivityBucketAttributes,
}


def validate_attributes(kind: CatalogKind, attributes: dict[str, Any] | None) -> dict[str, Any]:
    """Return the attributes of ``kind`` with defaults filled in, or raise ``CatalogError``."""
    schema = ATTRIBUTE_SCHEMAS.get(kind, _Attributes)
    try:
        return schema.model_validate(attributes or {}).model_dump()
    except ValidationError as err:
        raise CatalogError(f"Invalid attributes for {kind}: {err}") from err


def require_item(session: Session, item_id: int, kind: CatalogKind) -> CatalogItem:
    """The catalog item ``item_id``, checking that it belongs to ``kind`` and is active."""
    item = session.get(CatalogItem, item_id)
    if item is None or item.kind != kind:
        raise CatalogError(f"{item_id} is not a {kind} item")
    if not item.is_active:
        raise CatalogError(f"{kind} {item.code} is inactive")
    return item


# --- Audited CRUD ------------------------------------------------------------------------------
# Records are deactivated, never deleted: other features may already reference them.


def list_items(session: Session, kind: CatalogKind) -> list[CatalogItem]:
    stmt = (
        select(CatalogItem)
        .where(CatalogItem.kind == kind)
        .order_by(CatalogItem.sort_order, CatalogItem.name)
    )
    return list(session.scalars(stmt))


def create_item(
    session: Session,
    *,
    kind: CatalogKind,
    code: str,
    name: str,
    parent_id: int | None = None,
    sort_order: int = 0,
    attributes: dict[str, Any] | None = None,
    actor_id: int | None,
) -> CatalogItem:
    code = _clean(code)
    with transaction(session):
        if session.scalars(
            select(CatalogItem).where(CatalogItem.kind == kind, CatalogItem.code == code)
        ).first():
            raise DuplicateError(f"{kind} {code} already exists")
        item = CatalogItem(
            kind=kind,
            code=code,
            name=_clean(name),
            parent_id=_check_parent(session, kind, parent_id),
            sort_order=sort_order,
            attributes=validate_attributes(kind, attributes),
        )
        return _add(session, item, actor_id)


def update_item(
    session: Session,
    item: CatalogItem,
    *,
    name: str,
    sort_order: int,
    attributes: dict[str, Any] | None,
    actor_id: int | None,
) -> CatalogItem:
    """Change the name, order and attributes. ``kind`` and ``code`` never change."""
    values = {
        "name": _clean(name),
        "sort_order": sort_order,
        "attributes": validate_attributes(CatalogKind(item.kind), attributes),
    }
    return _update(session, item, values, actor_id)


def set_item_active(
    session: Session, item: CatalogItem, active: bool, *, actor_id: int | None
) -> CatalogItem:
    return _update(session, item, {"is_active": active}, actor_id)


def list_portfolios(session: Session) -> list[Portfolio]:
    return list(session.scalars(select(Portfolio).order_by(Portfolio.sort_order, Portfolio.name)))


def create_portfolio(
    session: Session, *, code: str, name: str, sort_order: int = 0, actor_id: int | None
) -> Portfolio:
    code = _clean(code)
    with transaction(session):
        _ensure_free(session, Portfolio, Portfolio.code, code)
        return _add(
            session, Portfolio(code=code, name=_clean(name), sort_order=sort_order), actor_id
        )


def update_portfolio(
    session: Session, portfolio: Portfolio, *, name: str, sort_order: int, actor_id: int | None
) -> Portfolio:
    return _update(session, portfolio, {"name": _clean(name), "sort_order": sort_order}, actor_id)


def set_portfolio_active(
    session: Session, portfolio: Portfolio, active: bool, *, actor_id: int | None
) -> Portfolio:
    return _update(session, portfolio, {"is_active": active}, actor_id)


def list_applications(session: Session) -> list[Application]:
    return list(session.scalars(select(Application).order_by(Application.name)))


def create_application(
    session: Session, *, code: str, name: str, type_id: int | None = None, actor_id: int | None
) -> Application:
    code = _clean(code)
    with transaction(session):
        _ensure_free(session, Application, Application.code, code)
        if type_id is not None:
            require_item(session, type_id, CatalogKind.APPLICATION_TYPE)
        return _add(session, Application(code=code, name=_clean(name), type_id=type_id), actor_id)


def update_application(
    session: Session,
    application: Application,
    *,
    name: str,
    type_id: int | None,
    actor_id: int | None,
) -> Application:
    if type_id is not None and type_id != application.type_id:
        require_item(session, type_id, CatalogKind.APPLICATION_TYPE)
    return _update(session, application, {"name": _clean(name), "type_id": type_id}, actor_id)


def set_application_active(
    session: Session, application: Application, active: bool, *, actor_id: int | None
) -> Application:
    return _update(session, application, {"is_active": active}, actor_id)


def list_repositories(session: Session) -> list[Repository]:
    return list(session.scalars(select(Repository).order_by(Repository.name)))


def create_repository(
    session: Session,
    *,
    name: str,
    url: str | None = None,
    application_id: int | None = None,
    actor_id: int | None,
) -> Repository:
    name = _clean(name)
    with transaction(session):
        _ensure_free(session, Repository, Repository.name, name)
        _require_application(session, application_id)
        repository = Repository(name=name, url=_clean(url) or None, application_id=application_id)
        return _add(session, repository, actor_id)


def update_repository(
    session: Session,
    repository: Repository,
    *,
    name: str,
    url: str | None,
    application_id: int | None,
    actor_id: int | None,
) -> Repository:
    name = _clean(name)
    with transaction(session):
        if name != repository.name:
            _ensure_free(session, Repository, Repository.name, name)
        _require_application(session, application_id)
        return _update(
            session,
            repository,
            {"name": name, "url": _clean(url) or None, "application_id": application_id},
            actor_id,
        )


def set_repository_active(
    session: Session, repository: Repository, active: bool, *, actor_id: int | None
) -> Repository:
    return _update(session, repository, {"is_active": active}, actor_id)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _ensure_free(session: Session, model: type[Base], column: Any, value: str) -> None:
    if session.scalars(select(model).where(column == value)).first() is not None:
        raise DuplicateError(f"{model.__tablename__} {value} already exists")


def _require_application(session: Session, application_id: int | None) -> None:
    if application_id is not None and session.get(Application, application_id) is None:
        raise CatalogError(f"Application {application_id} does not exist")


def _check_parent(session: Session, kind: CatalogKind, parent_id: int | None) -> int | None:
    """Parents only where the hierarchy allows them; reasons always have a category."""
    if parent_id is None:
        if kind == CatalogKind.REWORK_REASON:
            raise CatalogError("A rework reason needs a category")
        return None
    parent = session.get(CatalogItem, parent_id)
    if parent is None or CHILD_KINDS.get(CatalogKind(parent.kind)) != kind:
        raise CatalogError(f"{kind} cannot be placed under item {parent_id}")
    return parent.id


def _snapshot(entity: Any, fields: Mapping[str, Any]) -> dict[str, Any]:
    return {key: getattr(entity, key) for key in fields}


def _columns(entity: Any) -> dict[str, Any]:
    skip = {"id", "created_at"}
    return {c.key: getattr(entity, c.key) for c in entity.__table__.columns if c.key not in skip}


def _add[E: Base](session: Session, entity: E, actor_id: int | None) -> E:
    session.add(entity)
    session.flush()
    audit.record(
        session,
        action=AuditAction.CREATE,
        entity_type=entity.__tablename__,
        entity_id=entity.id,  # type: ignore[attr-defined]
        actor_id=actor_id,
        changes=_columns(entity),
    )
    return entity


def _update[E: Base](
    session: Session, entity: E, values: Mapping[str, Any], actor_id: int | None
) -> E:
    with transaction(session):
        before = _snapshot(entity, values)
        for key, value in values.items():
            setattr(entity, key, value)
        changes = audit.diff(before, values)
        if changes:
            audit.record(
                session,
                action=AuditAction.UPDATE,
                entity_type=entity.__tablename__,
                entity_id=entity.id,  # type: ignore[attr-defined]
                actor_id=actor_id,
                changes=changes,
            )
    return entity
