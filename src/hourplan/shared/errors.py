"""Global error handlers: HTML for browsers, JSON for API clients, never a traceback."""

import logging

from flask import Flask, Response, jsonify, render_template, request
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException, InternalServerError

log = logging.getLogger(__name__)

TEMPLATED_CODES = {400, 403, 404, 429, 500}


def _wants_json() -> bool:
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json"


def _render(error: HTTPException) -> tuple[Response | str, int]:
    code = error.code or 500
    if _wants_json():
        return jsonify(error=error.name, status=code), code
    template = f"errors/{code}.html" if code in TEMPLATED_CODES else "errors/generic.html"
    return render_template(template, error=error), code


def init_error_handlers(app: Flask) -> None:
    @app.errorhandler(CSRFError)
    def _csrf(error: CSRFError) -> tuple[Response | str, int]:
        return _render(error)

    @app.errorhandler(HTTPException)
    def _http(error: HTTPException) -> tuple[Response | str, int]:
        return _render(error)

    @app.errorhandler(Exception)
    def _unexpected(error: Exception) -> tuple[Response | str, int]:
        log.exception("Unhandled error", exc_info=error)
        return _render(InternalServerError())
