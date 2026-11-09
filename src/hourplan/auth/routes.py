import secrets
from urllib.parse import urlsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_user, logout_user
from werkzeug.wrappers import Response

from hourplan.auth import oidc, services
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
        return render_template(
            "auth/login.html", form=form, sso=oidc.is_enabled(current_app.config)
        ), 401
    status = 422 if form.is_submitted() else 200
    return render_template(
        "auth/login.html", form=form, sso=oidc.is_enabled(current_app.config)
    ), status


def _sso_redirect_uri() -> str:
    return url_for("auth.sso_callback", _external=True)


@bp.get("/login/sso")
@limiter.limit(_login_limit)
def sso_start() -> Response:
    if not oidc.is_enabled(current_app.config):
        abort(404)
    flow = oidc.new_flow()
    try:
        url = oidc.authorization_url(current_app.config, flow, _sso_redirect_uri())
    except oidc.OidcError:
        current_app.logger.exception("SSO discovery failed")
        flash("No se pudo iniciar el acceso con la cuenta de la organización.", "error")
        return redirect(url_for("auth.login"))
    session["oidc"] = flow
    return redirect(url)


@bp.get("/login/sso/callback")
@limiter.limit(_login_limit)
def sso_callback() -> Response:
    if not oidc.is_enabled(current_app.config):
        abort(404)
    flow = session.pop("oidc", None)
    code = request.args.get("code", "")
    state = request.args.get("state", "")
    failure = redirect(url_for("auth.login"))
    if not flow or not code or not secrets.compare_digest(state, flow["state"]):
        flash("No se pudo validar el acceso con la cuenta de la organización.", "error")
        return failure
    try:
        email = oidc.email_from_code(current_app.config, flow, code, _sso_redirect_uri())
    except oidc.OidcError:
        current_app.logger.warning("SSO rejected", exc_info=True)
        flash("No se pudo validar el acceso con la cuenta de la organización.", "error")
        return failure
    user = services.authenticate_sso(db.session, email=email)
    if user is None:
        flash(
            "Tu cuenta de la organización no tiene acceso a HourPlan. Pide que te den de alta.",
            "error",
        )
        return failure
    login_user(user)
    return redirect(url_for("main.index"))


@bp.post("/logout")
@login_required
def logout() -> Response:
    logout_user()
    flash("Sesión cerrada.", "info")
    return redirect(url_for("auth.login"))
