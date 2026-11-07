import tempfile
from datetime import date, datetime
from pathlib import Path

import click
from flask import Blueprint, current_app
from sqlalchemy import func, select

from hourplan.db import transaction
from hourplan.extensions import db
from hourplan.imports import demo, export, plan, services
from hourplan.imports.mapping import load_mapping
from hourplan.imports.models import ImportBatch, ImportIssue

bp = Blueprint("imports_cli", __name__, cli_group=None)


def _mapping_path() -> Path | None:
    overlay = current_app.config.get("HOURPLAN_OVERLAY_DIR")
    return Path(overlay) / "import_mapping.toml" if overlay else None


@bp.cli.command("import-hours")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def import_hours_command(file: Path) -> None:
    """Import the hours sheets of an Excel file (a repeated file or row is skipped)."""
    try:
        mapping = load_mapping(_mapping_path())
        with transaction(db.session):
            batch = services.import_hours(db.session, file, mapping=mapping)
            batch_id, stats = batch.id, batch.stats
    except (services.ImportRejectedError, ValueError) as err:
        raise click.ClickException(str(err)) from err
    click.echo(f"Lote {batch_id}:")
    for name, value in sorted(stats.items()):
        click.echo(f"  {name}: {value}")
    click.echo(f"Informe: flask import-report {batch_id}")


@bp.cli.command("import-plan")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def import_plan_command(file: Path) -> None:
    """Import the backlog and planning sheets of an Excel file (changed plans get a version)."""
    try:
        mapping = load_mapping(_mapping_path())
        with transaction(db.session):
            batch = plan.import_plan(db.session, file, mapping=mapping)
            batch_id, stats = batch.id, batch.stats
    except (services.ImportRejectedError, ValueError) as err:
        raise click.ClickException(str(err)) from err
    click.echo(f"Lote {batch_id}:")
    for name, value in sorted(stats.items()):
        click.echo(f"  {name}: {value}")
    click.echo(f"Informe: flask import-report {batch_id}")


@bp.cli.command("export-hours")
@click.argument("file", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--from", "start", type=click.DateTime(["%Y-%m-%d"]), help="Primer día (AAAA-MM-DD).")
@click.option("--to", "end", type=click.DateTime(["%Y-%m-%d"]), help="Último día (AAAA-MM-DD).")
def export_hours_command(file: Path, start: datetime | None, end: datetime | None) -> None:
    """Export the recorded hours to an Excel file with one sheet per resource."""
    first: date | None = start.date() if start else None
    last: date | None = end.date() if end else None
    try:
        totals = export.export_hours(
            db.session, file, load_mapping(_mapping_path()), start=first, end=last
        )
    except ValueError as err:
        raise click.ClickException(str(err)) from err
    click.echo(f"Archivo: {file}")
    for name, value in sorted(totals.items()):
        click.echo(f"  {name}: {value}")


@bp.cli.command("demo-data")
def demo_data_command() -> None:
    """Load fictitious hours and plan (only into a database without hours)."""
    try:
        mapping = load_mapping(_mapping_path())
        with tempfile.TemporaryDirectory() as workdir, transaction(db.session):
            stats = demo.load_demo(db.session, Path(workdir), mapping)
    except (demo.DemoRefusedError, services.ImportRejectedError, ValueError) as err:
        raise click.ClickException(str(err)) from err
    click.echo("Datos demo cargados:")
    for name, value in sorted(stats.items()):
        click.echo(f"  {name}: {value}")


@bp.cli.command("import-report")
@click.argument("batch_id", type=int)
@click.option("--limit", default=50, show_default=True, help="Hallazgos por código.")
def import_report_command(batch_id: int, limit: int) -> None:
    """List the data-quality findings of a batch."""
    batch = db.session.get(ImportBatch, batch_id)
    if batch is None:
        raise click.ClickException("Lote no encontrado.")
    click.echo(f"Lote {batch.id} ({batch.status}) · {batch.file_name}")
    summary = db.session.execute(
        select(ImportIssue.severity, ImportIssue.code, func.count())
        .where(ImportIssue.batch_id == batch_id)
        .group_by(ImportIssue.severity, ImportIssue.code)
        .order_by(ImportIssue.severity, ImportIssue.code)
    ).all()
    for severity, code, count in summary:
        click.echo(f"\n[{severity}] {code}: {count}")
        rows = db.session.scalars(
            select(ImportIssue)
            .where(ImportIssue.batch_id == batch_id, ImportIssue.code == code)
            .order_by(ImportIssue.id)
            .limit(limit)
        )
        for issue in rows:
            click.echo(f"  {issue.sheet} fila {issue.row_number}: {issue.message}")


@bp.cli.command("import-revert")
@click.argument("batch_id", type=int)
def import_revert_command(batch_id: int) -> None:
    """Undo a batch: removes its hours and the records it created if nothing else uses them."""
    batch = db.session.get(ImportBatch, batch_id)
    if batch is None:
        raise click.ClickException("Lote no encontrado.")
    try:
        with transaction(db.session):
            services.revert_batch(db.session, batch)
    except services.ImportRejectedError as err:
        raise click.ClickException(str(err)) from err
    click.echo(f"Lote {batch_id} revertido.")
