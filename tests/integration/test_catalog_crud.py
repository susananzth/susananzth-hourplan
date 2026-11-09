import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.audit.models import AuditLog
from hourplan.catalogs import services
from hourplan.catalogs.models import CatalogKind


def last_audit(session: Session, entity_type: str) -> AuditLog:
    stmt = (
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type)
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    return session.scalars(stmt).one()


def test_item_create_update_and_deactivate_are_audited(session: Session) -> None:
    item = services.create_item(
        session, kind=CatalogKind.PRIORITY, code=" HIGH ", name=" Alta ", actor_id=None
    )
    assert (item.code, item.name) == ("HIGH", "Alta")
    assert last_audit(session, "catalog_item").action == "create"

    services.update_item(
        session, item, name="Muy alta", sort_order=2, attributes=None, actor_id=None
    )
    changes = last_audit(session, "catalog_item").changes
    assert changes["name"] == ["Alta", "Muy alta"]
    assert changes["sort_order"] == [0, 2]

    services.set_item_active(session, item, False, actor_id=None)
    assert last_audit(session, "catalog_item").changes == {"is_active": [True, False]}
    # No change, no audit row.
    before = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    services.set_item_active(session, item, False, actor_id=None)
    assert session.scalars(select(AuditLog).order_by(AuditLog.id)).all() == before


def test_duplicate_code_is_rejected_per_kind(session: Session) -> None:
    services.create_item(session, kind=CatalogKind.PRIORITY, code="A", name="A", actor_id=None)
    with pytest.raises(services.DuplicateError):
        services.create_item(session, kind=CatalogKind.PRIORITY, code="A", name="B", actor_id=None)
    # Same code in another kind is fine.
    services.create_item(session, kind=CatalogKind.TASK_TYPE, code="A", name="A", actor_id=None)


def test_attributes_are_validated_and_filled(session: Session) -> None:
    status = services.create_item(
        session,
        kind=CatalogKind.PROJECT_STATUS,
        code="DONE",
        name="Cerrado",
        attributes={"is_closed": True},
        actor_id=None,
    )
    assert status.attributes == {"is_closed": True}
    with pytest.raises(services.CatalogError):
        services.create_item(
            session,
            kind=CatalogKind.PROJECT_STATUS,
            code="X",
            name="X",
            attributes={"nope": 1},
            actor_id=None,
        )


def test_hierarchy_rules(session: Session) -> None:
    category = services.create_item(
        session, kind=CatalogKind.REWORK_CATEGORY, code="C", name="C", actor_id=None
    )
    with pytest.raises(services.CatalogError, match="needs a category"):
        services.create_item(
            session, kind=CatalogKind.REWORK_REASON, code="R", name="R", actor_id=None
        )
    reason = services.create_item(
        session,
        kind=CatalogKind.REWORK_REASON,
        code="R",
        name="R",
        parent_id=category.id,
        actor_id=None,
    )
    assert reason.parent_id == category.id
    with pytest.raises(services.CatalogError, match="cannot be placed"):
        services.create_item(
            session,
            kind=CatalogKind.PRIORITY,
            code="P",
            name="P",
            parent_id=category.id,
            actor_id=None,
        )


def test_portfolio_application_repository_crud(session: Session) -> None:
    portfolio = services.create_portfolio(session, code="P1", name="Cartera", actor_id=None)
    with pytest.raises(services.DuplicateError):
        services.create_portfolio(session, code="P1", name="Otra", actor_id=None)
    services.update_portfolio(session, portfolio, name="Nueva", sort_order=3, actor_id=None)
    services.set_portfolio_active(session, portfolio, False, actor_id=None)
    assert last_audit(session, "portfolio").changes == {"is_active": [True, False]}

    app_type = services.create_item(
        session, kind=CatalogKind.APPLICATION_TYPE, code="WEB", name="Web", actor_id=None
    )
    other = services.create_item(
        session, kind=CatalogKind.PRIORITY, code="P", name="P", actor_id=None
    )
    application = services.create_application(
        session, code="APP", name="App", type_id=app_type.id, actor_id=None
    )
    with pytest.raises(services.CatalogError):
        services.create_application(session, code="B", name="B", type_id=other.id, actor_id=None)
    with pytest.raises(services.CatalogError):
        services.update_application(
            session, application, name="App", type_id=other.id, actor_id=None
        )
    services.set_application_active(session, application, False, actor_id=None)

    repo = services.create_repository(
        session, name="repo", url=" ", application_id=application.id, actor_id=None
    )
    assert repo.url is None
    with pytest.raises(services.DuplicateError):
        services.create_repository(session, name="repo", actor_id=None)
    with pytest.raises(services.CatalogError):
        services.create_repository(session, name="r2", application_id=-1, actor_id=None)
    services.update_repository(
        session, repo, name="repo", url="https://x.test/r", application_id=None, actor_id=None
    )
    assert last_audit(session, "repository").changes["url"] == [None, "https://x.test/r"]
    services.set_repository_active(session, repo, False, actor_id=None)
    assert [r.name for r in services.list_repositories(session)] == ["repo"]
    assert [a.code for a in services.list_applications(session)] == ["APP"]
    assert [p.code for p in services.list_portfolios(session)] == ["P1"]
    assert [i.code for i in services.list_items(session, CatalogKind.PRIORITY)] == ["P"]
