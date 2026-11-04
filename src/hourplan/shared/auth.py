"""Typed wrappers around Flask-Login (the library ships without type hints)."""

from collections.abc import Callable
from typing import cast

import flask_login


def login_required[F: Callable[..., object]](view: F) -> F:
    return cast(F, flask_login.login_required(view))
