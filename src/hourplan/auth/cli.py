import click
from flask import Blueprint

from hourplan.auth import services
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.extensions import db

bp = Blueprint("auth_cli", __name__, cli_group=None)

ROLE_CHOICE = click.Choice([role.value for role in Role])


@bp.cli.command("create-user")
@click.option("--email", prompt=True)
@click.option("--name", "display_name", prompt="Nombre visible")
@click.option("--role", "roles", multiple=True, type=ROLE_CHOICE, help="Repetible.")
@click.password_option()
def create_user_command(
    email: str, display_name: str, roles: tuple[str, ...], password: str
) -> None:
    """Create a user account."""
    try:
        user = services.create_user(
            db.session,
            email=email,
            display_name=display_name,
            password=password,
            roles=[Role(role) for role in roles],
        )
    except services.AuthError as err:
        raise click.ClickException(str(err)) from err
    click.echo(f"Usuario creado (id {user.id}).")


@bp.cli.command("grant-role")
@click.argument("email")
@click.argument("role", type=ROLE_CHOICE)
def grant_role_command(email: str, role: str) -> None:
    """Give a role to a user."""
    changed = services.grant_role(db.session, _user(email), Role(role), actor_id=None)
    click.echo("Rol asignado." if changed else "El usuario ya tenía ese rol.")


@bp.cli.command("revoke-role")
@click.argument("email")
@click.argument("role", type=ROLE_CHOICE)
def revoke_role_command(email: str, role: str) -> None:
    """Remove a role from a user."""
    changed = services.revoke_role(db.session, _user(email), Role(role), actor_id=None)
    click.echo("Rol retirado." if changed else "El usuario no tenía ese rol.")


def _user(email: str) -> User:
    user = services.find_by_email(db.session, email)
    if user is None:
        raise click.ClickException(f"No existe el usuario {email}")
    return user
