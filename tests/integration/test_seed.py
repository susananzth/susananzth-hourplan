from pathlib import Path

import pytest
from flask import Flask
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hourplan.catalogs.models import CatalogItem, CatalogKind
from hourplan.policies.models import EvaluationPolicy, WorkPolicy
from hourplan.projects.models import PhaseTemplate, PhaseType
from hourplan.setup import services


def count(session: Session, model: type[object]) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_default_seed_is_idempotent(session: Session) -> None:
    seed = services.load_seed(services.DEFAULT_SEED)

    first = services.apply_seed(session, seed)
    totals = {model: count(session, model) for model in (CatalogItem, PhaseTemplate)}
    second = services.apply_seed(session, seed)

    assert first.created["phase_template"] == 2
    assert second.created == {}
    assert {model: count(session, model) for model in totals} == totals
    assert count(session, EvaluationPolicy) == 1
    assert count(session, WorkPolicy) == 1


def test_default_seed_content(session: Session) -> None:
    services.apply_seed(session, services.load_seed(services.DEFAULT_SEED))

    default = session.scalars(select(PhaseTemplate).where(PhaseTemplate.is_default)).one()
    assert default.code == "STANDARD"
    assert [step.position for step in default.steps] == list(range(1, 8))
    assert {step.phase_type for step in default.steps} == set(PhaseType)

    incomplete = session.scalars(select(CatalogItem).where(CatalogItem.code == "INCOMPLETE")).one()
    assert incomplete.kind == CatalogKind.REWORK_REASON
    assert incomplete.parent is not None
    assert incomplete.parent.code == "EXECUTION"

    planning = session.scalars(select(CatalogItem).where(CatalogItem.code == "PLANNING")).one()
    assert planning.attributes == {"attributable_to_developer": False}


def test_seed_does_not_overwrite_existing_items(session: Session) -> None:
    session.add(CatalogItem(kind=CatalogKind.PRIORITY, code="HIGH", name="Urgente"))
    session.flush()

    services.apply_seed(session, services.load_seed(services.DEFAULT_SEED))

    high = session.scalars(select(CatalogItem).where(CatalogItem.code == "HIGH")).one()
    assert high.name == "Urgente"


def test_overlay_seed_replaces_the_default(tmp_path: Path, session: Session) -> None:
    assert services.seed_path(None) == services.DEFAULT_SEED
    assert services.seed_path(str(tmp_path)) == services.DEFAULT_SEED

    overlay = tmp_path / "seed.toml"
    overlay.write_text(
        '[[portfolio]]\ncode = "UNIT-A"\nname = "Unidad A"\n\n'
        '[[holiday]]\nday = 2026-12-25\nname = "Navidad"\n',
        encoding="utf-8",
    )
    assert services.seed_path(str(tmp_path)) == overlay

    report = services.apply_seed(session, services.load_seed(overlay))
    assert report.created == {"portfolio": 1, "holiday": 1}


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (
            '[[catalog]]\nkind = "priority"\nitems = [{code = "A", name = "A", children = '
            '[{code = "B", name = "B"}]}]\n',
            "cannot have children",
        ),
        ('[[catalog]]\nkind = "unknown"\nitems = []\n', "kind"),
        (
            '[[phase_template]]\ncode = "A"\nname = "A"\nis_default = true\nsteps = '
            '[{name = "X", phase_type = "planning"}]\n' * 2,
            "one phase template",
        ),
        ("unexpected = 1\n", "unexpected"),
    ],
)
def test_invalid_seed_is_rejected(tmp_path: Path, content: str, message: str) -> None:
    path = tmp_path / "seed.toml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValidationError, match=message):
        services.load_seed(path)


def test_invalid_catalog_attributes_are_rejected(tmp_path: Path, session: Session) -> None:
    path = tmp_path / "seed.toml"
    path.write_text(
        '[[catalog]]\nkind = "priority"\nitems = [{code = "A", name = "A", '
        "attributes = {is_closed = true}}]\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Invalid attributes"):
        services.apply_seed(session, services.load_seed(path))


def test_seed_command(app: Flask, tmp_path: Path) -> None:
    runner = app.test_cli_runner()

    assert "Semilla del núcleo:" in runner.invoke(args=["seed"]).output
    assert "nada que crear" in runner.invoke(args=["seed"]).output

    (tmp_path / "seed.toml").write_text("bad = [", encoding="utf-8")
    app.config["HOURPLAN_OVERLAY_DIR"] = str(tmp_path)
    result = runner.invoke(args=["seed"])
    assert result.exit_code != 0
    assert "Semilla no válida" in result.output
