import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.auth import services as auth
from hourplan.resources import services
from hourplan.resources.models import Resource


def _user(session: Session, email: str = "ana@example.test", name: str = "Ana Demo"):
    return auth.create_user(
        session, email=email, display_name=name, password="una-clave-larga-123", roles=[]
    )


def test_links_to_an_existing_free_resource_ignoring_case(session: Session) -> None:
    session.add(Resource(display_name="ana demo"))
    session.commit()
    user = _user(session)

    resource = services.link_user(session, user, actor_id=None)

    assert resource.user_id == user.id
    assert len(list(session.scalars(select(Resource)))) == 1


def test_creates_the_resource_when_none_matches(session: Session) -> None:
    user = _user(session)

    resource = services.link_user(session, user, "Otra Persona", actor_id=None)

    assert resource.display_name == "Otra Persona"
    assert resource.user_id == user.id


def test_refuses_a_second_link_and_a_taken_resource(session: Session) -> None:
    first = _user(session)
    second = _user(session, "luis@example.test", "Luis Demo")
    services.link_user(session, first, actor_id=None)

    with pytest.raises(services.ResourceError):
        services.link_user(session, first, "Otro", actor_id=None)
    with pytest.raises(services.ResourceError):
        services.link_user(session, second, "Ana Demo", actor_id=None)
