import ast
import re
from pathlib import Path

import pytest

from hourplan.shared.i18n import safe_return
from hourplan.shared.locales import EN

SRC = Path(__file__).parents[2] / "src" / "hourplan"
CALL = re.compile(r"""\b_\(\s*(["'])(.+?)\1\s*\)""")
JS_CALL = re.compile(r"""\bhp[TF]\(\s*(["'])(.+?)\1""")
# Screens only: command-line tools, spreadsheet import and the MCP server speak Spanish.
NOT_SCREENS = (
    "cli.py",
    "imports/",
    "mcp",
    "voice/extract.py",
    "config.py",
    "setup/",
    "auth/oidc.py",
    "auth/permissions.py",
)
# Internal errors that never reach a screen as they are.
INTERNAL = {
    "A justification is required for this change",
    "A rework reason needs a category",
    "Database is not initialised",
    "You cannot deactivate your own account",
    "You cannot remove your own admin role",
}
MARKERS = {"_", "N_", "flash"}
VALIDATORS = {"InputRequired", "DataRequired", "Length", "NumberRange", "Email", "Regexp"}


def _name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _text(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip():
        return node.value
    return None


def _python_texts(path: Path) -> set[str]:
    found: set[str] = set()
    screen = not any(part in str(path.relative_to(SRC)) for part in NOT_SCREENS)
    for node in ast.walk(ast.parse(path.read_text())):
        if (
            isinstance(node, ast.Raise)
            and screen
            and isinstance(node.exc, ast.Call)
            and node.exc.args
            and (text := _text(node.exc.args[0]))
            and text not in INTERNAL
        ):
            found.add(text)
        if not isinstance(node, ast.Call):
            continue
        name = _name(node.func)
        first = _text(node.args[0]) if node.args else None
        if (name in MARKERS or (name.endswith("Field") and screen)) and first:
            found.add(first)
        if name in VALIDATORS or name.endswith("Field"):
            for kw in node.keywords:
                if kw.arg in ("message", "description", "label") and (text := _text(kw.value)):
                    found.add(text)
            if name == "InputRequired" and first:
                found.add(first)
    return found


def used_texts() -> set[str]:
    found: set[str] = set()
    for path in SRC.rglob("*.html"):
        found.update(match.group(2) for match in CALL.finditer(path.read_text()))
    for path in SRC.rglob("*.js"):
        found.update(match.group(2) for match in JS_CALL.finditer(path.read_text()))
    for path in SRC.rglob("*.py"):
        if path.name != "locales.py":
            found |= _python_texts(path)
    return found


def test_every_translatable_text_has_an_english_entry() -> None:
    assert sorted(used_texts() - EN.keys()) == []


def test_english_entries_are_not_left_equal_or_empty() -> None:
    assert [k for k, v in EN.items() if not v.strip()] == []


# Text between tags or in a readable attribute that is not wrapped in _().
_TOKEN = re.compile(r"({#.*?#}|{%.*?%}|{{.*?}}|<script\b.*?</script>|<[^>]*>)", re.S)
_ATTR = re.compile(r'\b(?:aria-label|placeholder|title|alt)="([^"{]*[A-Za-zÁÉÍÓÚáéíóúñ][^"{]*)"')
_LETTER = re.compile(r"[A-Za-zÁÉÍÓÚáéíóúñ¿¡]")
_ALLOWED = {"HourPlan", "h", "· HourPlan"}


def test_templates_have_no_untranslated_text() -> None:
    loose = []
    for path in SRC.rglob("*.html"):
        for i, part in enumerate(_TOKEN.split(path.read_text())):
            text = part.strip()
            if i % 2 == 0 and _LETTER.search(text) and text not in _ALLOWED:
                loose.append(f"{path.name}: {text}")
            elif part.startswith("<"):
                loose += [f"{path.name}: {m}" for m in _ATTR.findall(part)]
    assert loose == []


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
