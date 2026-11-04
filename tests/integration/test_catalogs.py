import pytest
from sqlalchemy.orm import Session

from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.catalogs.services import CatalogError, require_item


def test_require_item_checks_kind_and_active(session: Session) -> None:
    item = CatalogItem(kind=CatalogKind.PRIORITY, code="HIGH", name="Alta")
    session.add(item)
    session.flush()

    assert require_item(session, item.id, CatalogKind.PRIORITY) is item
    with pytest.raises(CatalogError, match="is not a"):
        require_item(session, item.id, CatalogKind.TASK_TYPE)
    with pytest.raises(CatalogError, match="is not a"):
        require_item(session, -1, CatalogKind.PRIORITY)
    item.is_active = False
    with pytest.raises(CatalogError, match="inactive"):
        require_item(session, item.id, CatalogKind.PRIORITY)
