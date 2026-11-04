import click
from flask import Blueprint

from hourplan.auth import services
from hourplan.extensions import db

bp = Blueprint("auth_cli", __name__, cli_group=None)


@bp.cli.command("create-user")
@click.option("--email", prompt=True)
@click.option("--name", "display_name", prompt="Nombre visible")
@click.password_option()
def create_user_command(email: str, display_name: str, password: str) -> None:
    """Create a user account."""
    try:
        user = services.create_user(
            db.session, email=email, display_name=display_name, password=password
        )
    except services.AuthError as err:
        raise click.ClickException(str(err)) from err
    click.echo(f"Usuario creado (id {user.id}).")
