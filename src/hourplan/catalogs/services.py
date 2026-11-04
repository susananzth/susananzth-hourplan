"""Catalog rules shared by every feature that references a catalog item."""

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy.orm import Session

from hourplan.catalogs.models import CatalogItem, CatalogKind


class CatalogError(ValueError):
    pass


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
