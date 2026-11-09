from flask import Blueprint, redirect, render_template, request
from werkzeug.wrappers import Response

from hourplan.shared.auth import login_required
from hourplan.shared.i18n import COOKIE, COOKIE_DAYS, LANGUAGES, safe_return

bp = Blueprint("main", __name__, template_folder="templates")


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
