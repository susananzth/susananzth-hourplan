import re
from pathlib import Path

import pytest

from hourplan.shared.i18n import safe_return
from hourplan.shared.locales import EN

SRC = Path(__file__).parents[2] / "src" / "hourplan"
CALL = re.compile(r"""\b_\(\s*(["'])(.+?)\1\s*\)""")


def _used_texts() -> set[str]:
    found: set[str] = set()
    for path in [*SRC.rglob("*.html"), *SRC.rglob("*.py")]:
        if path.name == "locales.py":
            continue
        found.update(match.group(2) for match in CALL.finditer(path.read_text()))
    return found


def test_every_translatable_text_has_an_english_entry() -> None:
    assert sorted(_used_texts() - EN.keys()) == []


def test_english_entries_are_not_left_equal_or_empty() -> None:
    assert [k for k, v in EN.items() if not v.strip()] == []


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("/horas?x=1", "/horas?x=1"),
        ("https://evil.example/", "/"),
        ("//evil.example", "/"),
        ("/\\evil", "/"),
        (None, "/"),
        ("", "/"),
    ],
)
def test_safe_return(target: str | None, expected: str) -> None:
    assert safe_return(target) == expected
