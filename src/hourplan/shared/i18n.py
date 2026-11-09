"""Interface language for the web: which language a request uses, and its wiring.

The lookups themselves live in ``hourplan.shared.translate`` so services stay free of Flask.
"""

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from flask import Flask, has_request_context, request

from hourplan.shared import translate
from hourplan.shared.translate import DEFAULT, N_, _, translated

LANGUAGES = {"es": "Español", "en": "English"}
COOKIE = "lang"
COOKIE_DAYS = 365
_JS_CALL = re.compile(r'\bhp[TF]\(\s*"([^"]+)"')


def current_locale() -> str:
    if not has_request_context():
        return DEFAULT
    chosen = request.cookies.get(COOKIE)
    if chosen in LANGUAGES:
        return chosen
    return request.accept_languages.best_match(list(LANGUAGES), default=DEFAULT) or DEFAULT


def safe_return(target: str | None) -> str:
    """Only relative paths inside this site; anything else goes home."""
    if target and target.startswith("/") and "\\" not in target:
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc:
            return target
    return "/"


def init_app(app: Flask) -> None:
    translate.use_locale(current_locale)
    # A global, not only a context value, so imported macros can translate too.
    app.jinja_env.globals["_"] = _
    js_texts = sorted(
        {
            text
            for path in Path(app.static_folder or "").glob("js/*.js")
            for text in _JS_CALL.findall(path.read_text(encoding="utf-8"))
        }
    )

    @app.context_processor
    def _scripts() -> dict[str, Any]:
        """Texts the scripts show, in the current language (empty in Spanish, the source)."""
        if current_locale() == DEFAULT:
            return {"js_catalog": {}}
        return {"js_catalog": {text: _(text) for text in js_texts}}

    @app.context_processor
    def _inject() -> dict[str, Any]:
        return {"_": _, "locale": current_locale(), "languages": LANGUAGES}


__all__ = [
    "DEFAULT",
    "LANGUAGES",
    "N_",
    "_",
    "current_locale",
    "init_app",
    "safe_return",
    "translated",
]
