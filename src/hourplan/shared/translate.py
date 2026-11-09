"""Translation lookups, free of Flask: the web layer says which language is in use.

Spanish is the source text; other languages are lookups. A text with no entry in the
catalog is shown as written, so screens can be translated little by little.
"""

from collections.abc import Callable
from typing import Any

from hourplan.shared.locales import CATALOGS

DEFAULT = "es"
_current: Callable[[], str] = lambda: DEFAULT  # noqa: E731


def use_locale(getter: Callable[[], str]) -> None:
    """Tell the lookups how to find the current language (the web layer does it at start)."""
    global _current
    _current = getter


def current() -> str:
    return _current()


def _(text: str) -> str:
    """Translate ``text`` (written in Spanish) into the current language."""
    locale = _current()
    if locale == DEFAULT:
        return text
    return CATALOGS.get(locale, {}).get(text, text)


def N_(text: str) -> str:  # noqa: N802
    """Mark ``text`` for translation where it is defined; it is translated when shown."""
    return text


def translated[K, V](mapping: dict[K, V]) -> dict[K, V]:
    """A copy of a label mapping in the current language (tuples: their first item)."""
    out: dict[Any, Any] = {}
    for key, value in mapping.items():
        if isinstance(value, tuple):
            out[key] = (_(value[0]), *value[1:])
        else:
            out[key] = _(value) if isinstance(value, str) else value
    return out
