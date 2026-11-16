"""Pages of a query, cut in the database, for lists that grow without end (projects).

Small lists stay whole on the page and ``static/js/tables.js`` pages them in the browser.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import ClassVar

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

SIZES = (10, 20, 50, 100)
WINDOW = 5  # page numbers shown at once, as in the browser pager


@dataclass(frozen=True)
class Page[T]:
    items: Sequence[T]
    number: int
    size: int
    total: int
    sizes: ClassVar[tuple[int, ...]] = SIZES

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.size))

    @property
    def first_index(self) -> int:
        return (self.number - 1) * self.size + 1 if self.total else 0

    @property
    def last_index(self) -> int:
        return min(self.number * self.size, self.total)

    @property
    def window(self) -> range:
        start = max(1, min(self.number - WINDOW // 2, self.pages - WINDOW + 1))
        return range(start, min(self.pages, start + WINDOW - 1) + 1)

    def map[U](self, fn: Callable[[Sequence[T]], Sequence[U]]) -> Page[U]:
        """Same page, items turned into something else (all at once, so it can batch)."""
        return Page(fn(self.items), self.number, self.size, self.total)


def _positive(raw: str | None) -> int | None:
    try:
        value = int(raw or "")
    except ValueError:
        return None
    return value if value > 0 else None


def page_args(args: Mapping[str, str]) -> tuple[int, int]:
    """Page number and size from the query string (``pagina``, ``tamano``), always valid."""
    size = _positive(args.get("tamano"))
    return _positive(args.get("pagina")) or 1, size if size in SIZES else SIZES[0]


def paginate[T](session: Session, stmt: Select[T], number: int, size: int) -> Page[T]:
    """One page of ``stmt``, which must already be ordered. Past the end gives the last page."""
    total = session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    number = min(max(1, number), Page([], number, size, total).pages)
    items = list(session.scalars(stmt.limit(size).offset((number - 1) * size)))
    return Page(items, number, size, total)
