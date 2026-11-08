"""Minimal MCP server (Streamable HTTP, stateless, JSON replies). No Flask here.

An AI client can only read open project codes and leave *drafts*; real hours are saved
only when the person confirms them on the web page.
"""

import json
from typing import Any

from sqlalchemy.orm import Session

from hourplan.ailog import services
from hourplan.ailog.models import AiDraft
from hourplan.ailog.services import AilogError, Caller

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SERVER_INFO = {"name": "hourplan", "title": "HourPlan", "version": "1"}
INSTRUCTIONS = (
    "Registra horas de trabajo en HourPlan como borradores. Usa buscar_proyectos para "
    "obtener el código exacto, ver_borradores para no repetir líneas y proponer_horas para "
    "dejarlas en borrador. La persona las revisa y confirma en la web; nada se guarda solo."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "buscar_proyectos",
        "title": "Buscar proyectos",
        "description": "Proyectos y tickets abiertos cuyo código o título contiene el texto.",
        "inputSchema": {
            "type": "object",
            "properties": {"texto": {"type": "string", "maxLength": 60}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "ver_borradores",
        "title": "Ver borradores pendientes",
        "description": "Líneas propuestas antes que la persona aún no revisa.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "proponer_horas",
        "title": "Proponer horas",
        "description": (
            "Deja líneas de trabajo en borrador para que la persona las confirme. "
            "Los minutos se redondean al cuarto de hora."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "lineas": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": services.MAX_PROPOSED,
                    "items": {
                        "type": "object",
                        "properties": {
                            "fecha": {"type": "string", "format": "date"},
                            "codigo": {"type": "string", "maxLength": 40},
                            "minutos": {"type": "integer", "minimum": 1, "maximum": 1440},
                            "descripcion": {"type": "string", "maxLength": 2000},
                        },
                        "required": ["fecha", "codigo", "minutos"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["lineas"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
]


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _text(text: str, data: dict[str, Any] | None = None, *, error: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": error}
    if data is not None:
        out["structuredContent"] = data
    return out


def _draft_view(d: AiDraft) -> dict[str, Any]:
    return {
        "fecha": d.work_date.isoformat(),
        "codigo": d.code,
        "horas": str(d.hours),
        "descripcion": d.description or "",
    }


def call_tool(
    session: Session, caller: Caller, name: str, args: dict[str, Any], review_url: str
) -> dict[str, Any]:
    try:
        if name == "buscar_proyectos":
            found = services.search_projects(session, str(args.get("texto") or ""))
            items = [{"codigo": p.code, "titulo": p.title, "tipo": p.kind} for p in found]
            lines = [f"{i['codigo']} · {i['titulo']} ({i['tipo']})" for i in items]
            return _text(
                "\n".join(lines) or "No hay proyectos abiertos con ese texto.", {"proyectos": items}
            )
        if name == "ver_borradores":
            drafts = [_draft_view(d) for d in services.pending_drafts(session, caller.resource_id)]
            text = json.dumps(drafts, ensure_ascii=False) if drafts else "No hay borradores."
            return _text(text, {"borradores": drafts})
        if name == "proponer_horas":
            saved = services.propose(session, caller, args.get("lineas"))
            return _text(
                f"Quedaron {saved} líneas en borrador. La persona debe revisarlas y "
                f"confirmarlas en {review_url}",
                {"guardadas": saved, "revisar_en": review_url},
            )
    except AilogError as err:
        session.rollback()
        return _text(str(err), error=True)
    raise KeyError(name)


def handle(
    session: Session, caller: Caller, message: Any, review_url: str
) -> dict[str, Any] | None:
    """One JSON-RPC message in, one reply out (None for notifications)."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "Invalid Request")
    msg_id = message.get("id")
    method = message.get("method")
    if "id" not in message:
        return None
    if not isinstance(method, str):
        return _error(msg_id, -32600, "Invalid Request")
    params = message.get("params") or {}
    if not isinstance(params, dict):
        return _error(msg_id, -32602, "Invalid params")
    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(msg_id, -32602, "Invalid params")
        try:
            return _result(msg_id, call_tool(session, caller, str(name), args, review_url))
        except KeyError:
            return _error(msg_id, -32602, f"Unknown tool: {name}")
    return _error(msg_id, -32601, "Method not found")
