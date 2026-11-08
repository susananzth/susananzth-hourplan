import click
from flask import Blueprint

from hourplan.auth import services as auth
from hourplan.extensions import db
from hourplan.resources import services

bp = Blueprint("resources_cli", __name__, cli_group=None)


@bp.cli.command("link-resource")
@click.argument("email")
@click.option("--name", help="Nombre del recurso; por defecto, el nombre visible del usuario.")
def link_resource_command(email: str, name: str | None) -> None:
    """Link a user to a resource so they can log hours (reuses a free resource of that name)."""
    user = auth.find_by_email(db.session, email)
    if user is None:
        raise click.ClickException(f"No existe el usuario {email}")
    try:
        resource = services.link_user(db.session, user, name, actor_id=None)
    except services.ResourceError as err:
        raise click.ClickException(str(err)) from err
    click.echo(f"Usuario vinculado al recurso {resource.display_name} (id {resource.id}).")
