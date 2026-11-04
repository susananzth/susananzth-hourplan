from flask import Blueprint, render_template

from hourplan.shared.auth import login_required

bp = Blueprint("main", __name__, template_folder="templates")


@bp.get("/")
@login_required
def index() -> str:
    return render_template("main/index.html")
