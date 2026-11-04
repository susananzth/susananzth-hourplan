from enum import StrEnum


class Role(StrEnum):
    """Fixed roles. Permissions are code, not data (ADR-005)."""

    ADMIN = "admin"
    PM = "pm"
    LEAD = "lead"
    DEVELOPER = "developer"
    OBSERVER = "observer"
