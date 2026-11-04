"""Imports every model so Alembic sees the full metadata."""

from hourplan.auth.models import User
from hourplan.db import Base

metadata = Base.metadata

__all__ = ["User", "metadata"]
