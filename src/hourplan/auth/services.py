"""Authentication use cases. They receive the session; they never touch Flask."""

from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.auth.models import User
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


def normalize_email(email: str) -> str:
    return email.strip().lower()


def find_by_email(session: Session, email: str) -> User | None:
    stmt = select(User).where(func.lower(User.email) == normalize_email(email))
    return session.scalars(stmt).one_or_none()


def get_active_user(session: Session, user_id: int) -> User | None:
    user = session.get(User, user_id)
    return user if user is not None and user.is_active else None


def create_user(session: Session, *, email: str, display_name: str, password: str) -> User:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Password must have at least {MIN_PASSWORD_LENGTH} characters")
    with transaction(session):
        if find_by_email(session, email) is not None:
            raise EmailAlreadyRegisteredError(normalize_email(email))
        user = User(
            email=normalize_email(email),
            display_name=display_name.strip(),
            password_hash=_hasher.hash(password),
        )
        session.add(user)
    return user


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


def _verify(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError, InvalidHashError:
        return False
