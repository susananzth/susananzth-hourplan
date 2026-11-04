"""Role checks for views. Always enforced on the server."""

from collections.abc import Callable
from functools import wraps
from typing import Any, cast

import flask_login
from flask import abort, current_app

from hourplan.auth.roles import Role


def roles_required[F: Callable[..., Any]](*roles: Role) -> Callable[[F], F]:
    """Allow the view only to authenticated users holding at least one of ``roles``."""
    if not roles:
        raise ValueError("roles_required needs at least one role")

    def decorator(view: F) -> F:
        @wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            user = flask_login.current_user
            if not user.is_authenticated:
                return current_app.login_manager.unauthorized()  # type: ignore[attr-defined]
            if not user.has_role(*roles):
                abort(403)
            return view(*args, **kwargs)

        return cast(F, wrapped)

    return decorator
