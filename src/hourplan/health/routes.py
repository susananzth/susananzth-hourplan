import logging

from flask import Blueprint, Response, jsonify
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from hourplan.extensions import db, limiter

log = logging.getLogger(__name__)

bp = Blueprint("health", __name__)


@bp.get("/healthz")
@limiter.exempt
def healthz() -> tuple[Response, int]:
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.warning("Health check: database unavailable")
        return jsonify(status="error", database="unavailable"), 503
    return jsonify(status="ok", database="ok"), 200
