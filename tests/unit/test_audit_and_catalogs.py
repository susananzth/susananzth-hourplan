from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from hourplan.audit.services import diff, to_json
from hourplan.auth.roles import Role
from hourplan.catalogs.models import CatalogKind
from hourplan.catalogs.services import CatalogError, validate_attributes


def test_to_json_converts_non_json_values() -> None:
    value = {
        "hours": Decimal("1.50"),
        "day": date(2026, 1, 5),
        "at": datetime(2026, 1, 5, 8, tzinfo=UTC),
        "role": Role.PM,
        "items": (1, {Role.LEAD}),
    }

    assert to_json(value) == {
        "hours": "1.50",
        "day": "2026-01-05",
        "at": "2026-01-05T08:00:00+00:00",
        "role": "pm",
        "items": [1, ["lead"]],
    }


def test_diff_keeps_only_changed_fields() -> None:
    assert diff({"a": 1, "b": Decimal(2)}, {"a": 1, "b": Decimal(3)}) == {"b": ["2", "3"]}


def test_validate_attributes_fills_defaults_and_rejects_unknown_keys() -> None:
    assert validate_attributes(CatalogKind.PROJECT_STATUS, None) == {"is_closed": False}
    assert validate_attributes(CatalogKind.PRIORITY, {}) == {}
    with pytest.raises(CatalogError):
        validate_attributes(CatalogKind.PRIORITY, {"color": "red"})
    with pytest.raises(CatalogError):
        validate_attributes(CatalogKind.ACTIVITY_BUCKET, {"is_contingency": "yes"})
