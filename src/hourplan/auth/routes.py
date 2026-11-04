from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from werkzeug.wrappers import Response

from hourplan.auth import services
from hourplan.auth.forms import LoginForm
from hourplan.auth.models import User
from hourplan.extensions import db, limiter, login_manager
from hourplan.shared.auth import login_required

bp = Blueprint("auth", __name__, template_folder="templates")


def _load_user(user_id: str) -> User | None:
    if not user_id.isdigit():
        return None
    return services.get_active_user(db.session, int(user_id))


login_manager.user_loader(_load_user)


def _safe_next(target: str | None) -> str:
    """Only allow relative redirects inside this site."""
    if target:
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc and target.startswith("/") and "\\" not in target:
            return target
    return url_for("main.index")


def _login_limit() -> str:
    return str(current_app.config["LOGIN_RATE_LIMIT"])


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit(_login_limit, methods=["POST"])
def login() -> Response | str | tuple[str, int]:
    if current_user.is_authenticated:
        return redirect(url_for("main.index"))
    form = LoginForm()
    if form.validate_on_submit():
        user = services.authenticate(
            db.session, email=form.email.data or "", password=form.password.data or ""
        )
        if user is not None:
            login_user(user, remember=bool(form.remember.data))
            return redirect(_safe_next(request.args.get("next")))
        flash("Correo o contraseña incorrectos.", "error")
        return render_template("auth/login.html", form=form), 401
    status = 422 if form.is_submitted() else 200
    return render_template("auth/login.html", form=form), status


@bp.post("/logout")
@login_required
def logout() -> Response:
    logout_user()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("auth.login"))
