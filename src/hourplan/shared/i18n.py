"""Light interface translation: Spanish is the source text, other languages are lookups.

A text with no entry in the catalog is shown as written (Spanish), so screens can be
translated little by little without breaking anything.
"""

from typing import Any
from urllib.parse import urlsplit

from flask import Flask, has_request_context, request

from hourplan.shared.locales import CATALOGS

DEFAULT = "es"
LANGUAGES = {"es": "Español", "en": "English"}
COOKIE = "lang"
COOKIE_DAYS = 365


def current_locale() -> str:
    if not has_request_context():
        return DEFAULT
    chosen = request.cookies.get(COOKIE)
    if chosen in LANGUAGES:
        return chosen
    return request.accept_languages.best_match(list(LANGUAGES), default=DEFAULT) or DEFAULT


def _(text: str) -> str:
    """Translate ``text`` (written in Spanish) into the current language."""
    locale = current_locale()
    if locale == DEFAULT:
        return text
    return CATALOGS.get(locale, {}).get(text, text)


def safe_return(target: str | None) -> str:
    """Only relative paths inside this site; anything else goes home."""
    if target and target.startswith("/") and "\\" not in target:
        parts = urlsplit(target)
        if not parts.scheme and not parts.netloc:
            return target
    return "/"


def init_app(app: Flask) -> None:
    @app.context_processor
    def _inject() -> dict[str, Any]:
        return {"_": _, "locale": current_locale(), "languages": LANGUAGES}
