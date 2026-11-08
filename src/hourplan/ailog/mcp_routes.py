"""HTTP endpoint for MCP clients (Claude Code and others), authenticated by a personal token."""

import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, jsonify, request, url_for
from flask_limiter.util import get_remote_address
from werkzeug.wrappers import Response

from hourplan.ailog import mcp, services
from hourplan.extensions import csrf, db, limiter

bp = Blueprint("mcp", __name__)
csrf.exempt(bp)


def _key() -> str:
    return f"mcp:{get_remote_address()}"


def _plain(status: int, message: str, headers: dict[str, str] | None = None) -> Response:
    return Response(message, status=status, mimetype="text/plain", headers=headers)


@bp.route("/mcp", methods=["GET", "DELETE"])
def no_stream() -> Response:
    if not current_app.config.get("MCP_ENABLED"):
        abort(404)
    return _plain(405, "Use POST.", {"Allow": "POST"})


@bp.post("/mcp")
@limiter.limit(lambda: str(current_app.config["MCP_RATE_LIMIT"]), key_func=_key)
def endpoint() -> Response | tuple[Response, int]:
    if not current_app.config.get("MCP_ENABLED"):
        abort(404)
    origin = request.headers.get("Origin")
    if origin and urlsplit(origin).netloc != request.host:
        return _plain(403, "Origin not allowed.")
    version = request.headers.get("MCP-Protocol-Version")
    if version and version not in mcp.PROTOCOL_VERSIONS:
        return _plain(400, "Unsupported MCP-Protocol-Version.")
    scheme, _, plain = request.headers.get("Authorization", "").partition(" ")
    caller = None
    if scheme.lower() == "bearer":
        caller = services.authenticate(db.session, plain.strip(), datetime.now(UTC))
    if caller is None:
        return _plain(401, "Invalid or missing token.", {"WWW-Authenticate": "Bearer"})
    try:
        message: Any = json.loads(request.get_data(as_text=True) or "null")
    except ValueError:
        return jsonify(mcp._error(None, -32700, "Parse error")), 400
    reply = mcp.handle(db.session, caller, message, url_for("ailog.pending", _external=True))
    if reply is None:
        return Response(status=202)
    return jsonify(reply), 200
