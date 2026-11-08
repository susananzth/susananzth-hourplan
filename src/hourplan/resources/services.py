"""Resource use cases: linking a person to a user account."""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.auth.models import User
from hourplan.db import transaction
from hourplan.resources.models import Resource


class ResourceError(ValueError):
    pass


def link_user(
    session: Session, user: User, name: str | None = None, *, actor_id: int | None
) -> Resource:
    """Link ``user`` to the resource called ``name`` (their own name by default).

    An existing resource without account is reused (for example one created by an import);
    otherwise a new one is created.
    """
    current = session.scalar(select(Resource).where(Resource.user_id == user.id))
    if current is not None:
        raise ResourceError(f"Este usuario ya está vinculado al recurso {current.display_name}.")
    wanted = (name or user.display_name).strip()
    if not wanted:
        raise ResourceError("Indica el nombre del recurso.")
    with transaction(session):
        resource = session.scalar(
            select(Resource).where(func.lower(Resource.display_name) == wanted.lower())
        )
        if resource is not None and resource.user_id is not None:
            raise ResourceError(f"El recurso {resource.display_name} ya tiene otro usuario.")
        created = resource is None
        if resource is None:
            resource = Resource(display_name=wanted)
            session.add(resource)
        resource.user_id = user.id
        session.flush()
        audit.record(
            session,
            action=AuditAction.CREATE if created else AuditAction.UPDATE,
            entity_type="resource",
            entity_id=resource.id,
            actor_id=actor_id,
            changes={"user_id": user.id},
        )
    return resource
