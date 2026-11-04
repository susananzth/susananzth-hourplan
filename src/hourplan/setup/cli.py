import click
from flask import Blueprint, current_app

from hourplan.extensions import db
from hourplan.setup import services

bp = Blueprint("setup_cli", __name__, cli_group=None)


@bp.cli.command("seed")
def seed_command() -> None:
    """Load the initial configuration (only creates what is missing)."""
    path = services.seed_path(current_app.config.get("HOURPLAN_OVERLAY_DIR"))
    try:
        seed = services.load_seed(path)
    except (OSError, ValueError) as err:
        raise click.ClickException(f"Semilla no válida ({path.name}): {err}") from err
    report = services.apply_seed(db.session, seed)
    origin = "overlay" if path != services.DEFAULT_SEED else "núcleo"
    if not report.created:
        click.echo(f"Semilla del {origin}: nada que crear.")
        return
    click.echo(f"Semilla del {origin}:")
    for entity, count in sorted(report.created.items()):
        click.echo(f"  {entity}: {count}")
