import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "hourplan"
FORBIDDEN_CALLS = {"print", "breakpoint"}
FORBIDDEN_MODULES = {"pdb", "ipdb"}


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) in FORBIDDEN_CALLS:
            found.append(f"{path.name}:{node.lineno} {node.func.id}()")  # type: ignore[attr-defined]
        if isinstance(node, ast.Import | ast.ImportFrom):
            names = [alias.name for alias in node.names]
            module = getattr(node, "module", None)
            if FORBIDDEN_MODULES & {*names, module}:
                found.append(f"{path.name}:{node.lineno} import pdb")
    return found


@pytest.mark.parametrize("path", sorted(SRC.rglob("*.py")), ids=lambda p: str(p.relative_to(SRC)))
def test_no_debug_code(path: Path) -> None:
    assert _violations(path) == []
