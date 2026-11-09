from flask import Blueprint, redirect, render_template, request, url_for
from markupsafe import Markup
from werkzeug.wrappers import Response

from hourplan.main import nav
from hourplan.shared.auth import login_required
from hourplan.shared.i18n import COOKIE, COOKIE_DAYS, LANGUAGES, safe_return

bp = Blueprint("main", __name__, template_folder="templates")
bp.app_context_processor(nav.navigation)


@bp.app_template_global()
def icon(name: str, size: str = "size-4") -> Markup:
    """A decorative Lucide icon from the sprite built by ``npm run vendor``."""
    sprite = url_for("static", filename="vendor/icons.svg")
    return Markup(
        '<svg class="{} shrink-0" aria-hidden="true" focusable="false" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<use href="{}#{}"></use></svg>'
    ).format(size, sprite, name)


@bp.get("/")
@login_required
def index() -> str:
    return render_template("main/index.html")


@bp.post("/idioma")
def set_language() -> Response:
    response = redirect(safe_return(request.form.get("next")))
    lang = request.form.get("lang", "")
    if lang in LANGUAGES:
        response.set_cookie(
            COOKIE,
            lang,
            max_age=COOKIE_DAYS * 86400,
            samesite="Lax",
            httponly=True,
            secure=request.is_secure,
        )
    return response
