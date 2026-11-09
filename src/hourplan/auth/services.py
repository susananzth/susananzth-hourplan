"""Authentication use cases. They receive the session; they never touch Flask."""

from collections.abc import Iterable
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.auth.models import User, UserRole
from hourplan.auth.roles import Role
from hourplan.db import transaction

MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()
# Hash of a random value: verifying against it keeps the timing similar for unknown emails.
_DUMMY_HASH = _hasher.hash("not-a-real-password-hourplan")


class AuthError(Exception):
    """Base class for authentication errors."""


class EmailAlreadyRegisteredError(AuthError):
    pass


class WeakPasswordError(AuthError):
    pass


class SelfLockoutError(AuthError):
    """An admin tried to remove their own access."""


def normalize_email(email: str) -> str:
    return email.strip().lower()


def find_by_email(session: Session, email: str) -> User | None:
    stmt = select(User).where(func.lower(User.email) == normalize_email(email))
    return session.scalars(stmt).one_or_none()


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(func.lower(User.display_name), User.id)))


def set_user_active(session: Session, user: User, active: bool, *, actor_id: int | None) -> None:
    if not active and user.id == actor_id:
        raise SelfLockoutError("You cannot deactivate your own account")
    with transaction(session):
        if user.is_active == active:
            return
        user.is_active = active
        audit.record(
            session,
            action=AuditAction.UPDATE,
            entity_type="user",
            entity_id=user.id,
            actor_id=actor_id,
            changes={"is_active": [not active, active]},
            subject_user_id=user.id,
        )


def get_active_user(session: Session, user_id: int) -> User | None:
    user = session.get(User, user_id)
    return user if user is not None and user.is_active else None


def create_user(
    session: Session,
    *,
    email: str,
    display_name: str,
    password: str,
    roles: Iterable[Role] = (),
    actor_id: int | None = None,
) -> User:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Password must have at least {MIN_PASSWORD_LENGTH} characters")
    with transaction(session):
        if find_by_email(session, email) is not None:
            raise EmailAlreadyRegisteredError(normalize_email(email))
        user = User(
            email=normalize_email(email),
            display_name=display_name.strip(),
            password_hash=_hasher.hash(password),
            role_links=[UserRole(role=role.value) for role in sorted(set(roles))],
        )
        session.add(user)
        session.flush()
        audit.record(
            session,
            action=AuditAction.CREATE,
            entity_type="user",
            entity_id=user.id,
            actor_id=actor_id,
            changes={"email": user.email, "roles": sorted(user.roles)},
            subject_user_id=user.id,
        )
    return user


def grant_role(session: Session, user: User, role: Role, *, actor_id: int | None) -> bool:
    """Give ``role`` to ``user``. Returns ``False`` if they already had it."""
    with transaction(session):
        if role in user.roles:
            return False
        user.role_links.append(UserRole(role=role.value))
        _audit_role(session, user, AuditAction.CREATE, role, actor_id)
    return True


def revoke_role(session: Session, user: User, role: Role, *, actor_id: int | None) -> bool:
    """Remove ``role`` from ``user``. Returns ``False`` if they did not have it."""
    if role == Role.ADMIN and user.id == actor_id:
        raise SelfLockoutError("You cannot remove your own admin role")
    with transaction(session):
        link = next((link for link in user.role_links if link.role == role), None)
        if link is None:
            return False
        user.role_links.remove(link)
        _audit_role(session, user, AuditAction.DELETE, role, actor_id)
    return True


def _audit_role(
    session: Session, user: User, action: AuditAction, role: Role, actor_id: int | None
) -> None:
    audit.record(
        session,
        action=action,
        entity_type="user_role",
        entity_id=user.id,
        actor_id=actor_id,
        changes={"role": role},
        subject_user_id=user.id,
    )


def authenticate(session: Session, *, email: str, password: str) -> User | None:
    """Return the active user whose credentials match, or ``None``."""
    user = find_by_email(session, email)
    if user is None or not user.is_active:
        _verify(_DUMMY_HASH, password)
        return None
    if not _verify(user.password_hash, password):
        return None
    with transaction(session):
        if _hasher.check_needs_rehash(user.password_hash):
            user.password_hash = _hasher.hash(password)
        user.last_login_at = datetime.now(UTC)
    return user


def authenticate_sso(session: Session, *, email: str) -> User | None:
    """Return the active local user for an email the identity provider verified."""
    user = find_by_email(session, email)
    if user is None or not user.is_active:
        return None
    with transaction(session):
        user.last_login_at = datetime.now(UTC)
    return user


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False
