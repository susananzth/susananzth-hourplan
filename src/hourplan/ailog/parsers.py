"""Read the AI work log (CSV or Markdown table) and calendar files (.ics). Pure functions."""

import csv
import io
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

QUARTER = Decimal("0.25")
MAX_ROWS = 200

DATE_NAMES = ("fecha", "date")
CODE_NAMES = ("codigo", "código", "proyecto", "ticket", "code", "project")
MINUTE_NAMES = ("minutos", "minutes")
HOUR_NAMES = ("horas", "hours")
TEXT_NAMES = ("descripcion", "descripción", "tarea", "description")


class ParseError(Exception):
    """The file cannot be read; the message is shown to the person."""


@dataclass(frozen=True)
class LogRow:
    line: int
    work_date: date
    code: str
    hours: Decimal
    description: str | None


@dataclass(frozen=True)
class Meeting:
    work_date: date
    hours: Decimal
    title: str


def to_quarters(hours: Decimal) -> Decimal:
    """Nearest quarter hour, never below one quarter."""
    rounded = (hours / QUARTER).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * QUARTER
    return max(rounded, QUARTER)


def _number(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", ".").strip())
    except InvalidOperation as err:
        raise ValueError("no es un número") from err


def _column(header: list[str], names: tuple[str, ...]) -> int | None:
    for index, cell in enumerate(header):
        if cell.strip().lower() in names:
            return index
    return None


def _rows_from_table(table: list[list[str]]) -> list[LogRow]:
    if not table:
        raise ParseError("El archivo está vacío.")
    header = table[0]
    d, c = _column(header, DATE_NAMES), _column(header, CODE_NAMES)
    m, h = _column(header, MINUTE_NAMES), _column(header, HOUR_NAMES)
    t = _column(header, TEXT_NAMES)
    if d is None or c is None or (m is None and h is None):
        raise ParseError(
            "Faltan columnas. La primera fila debe tener: fecha, codigo, minutos "
            "(o horas) y descripcion."
        )
    rows: list[LogRow] = []
    for number, cells in enumerate(table[1:], start=2):
        if not any(cell.strip() for cell in cells):
            continue
        if len(rows) >= MAX_ROWS:
            raise ParseError(f"El archivo tiene más de {MAX_ROWS} líneas; divídelo.")

        def cell(index: int | None, cells: list[str] = cells) -> str:
            return cells[index].strip() if index is not None and index < len(cells) else ""

        try:
            work_date = date.fromisoformat(cell(d))
        except ValueError as err:
            raise ParseError(f"Línea {number}: la fecha debe ser AAAA-MM-DD.") from err
        try:
            hours = _number(cell(m)) / 60 if m is not None and cell(m) else _number(cell(h))
        except ValueError as err:
            raise ParseError(f"Línea {number}: los minutos u horas {err}.") from err
        if hours <= 0:
            raise ParseError(f"Línea {number}: la duración debe ser mayor que cero.")
        rows.append(LogRow(number, work_date, cell(c), to_quarters(hours), cell(t) or None))
    if not rows:
        raise ParseError("El archivo no tiene líneas de trabajo.")
    return rows


def parse_csv(text: str) -> list[LogRow]:
    sample = text[:2000]
    delimiter = ";" if sample.count(";") > sample.count(",") else ","
    return _rows_from_table(list(csv.reader(io.StringIO(text), delimiter=delimiter)))


def parse_markdown(text: str) -> list[LogRow]:
    table: list[list[str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(cells):
            continue
        table.append(cells)
    if not table:
        raise ParseError("No se encontró ninguna tabla en el archivo.")
    return _rows_from_table(table)


def parse_log(name: str, text: str) -> list[LogRow]:
    if name.lower().endswith((".md", ".markdown")):
        return parse_markdown(text)
    return parse_csv(text)


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _moment(key: str, value: str) -> datetime | None:
    """Date-time of DTSTART/DTEND; None for all-day events (they are not meetings)."""
    if "VALUE=DATE" in key.upper() and "VALUE=DATE-TIME" not in key.upper():
        return None
    try:
        wall = datetime.strptime(value.strip().rstrip("Z"), "%Y%m%dT%H%M%S")  # noqa: DTZ007
        return wall.replace(tzinfo=UTC)  # only to subtract; the wall time is kept as written
    except ValueError:
        return None


def _unescape(value: str) -> str:
    return value.replace("\\n", " ").replace("\\,", ",").replace(r"\;", ";").strip()


def parse_ics(text: str) -> list[Meeting]:
    """Timed events only. The times are taken as written; recurrences are not expanded."""
    meetings: list[Meeting] = []
    event: dict[str, str] | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            event = {}
        elif line == "END:VEVENT" and event is not None:
            start = _moment(event.get("DTSTART_KEY", ""), event.get("DTSTART", ""))
            end = _moment(event.get("DTEND_KEY", ""), event.get("DTEND", ""))
            cancelled = event.get("STATUS", "").upper() == "CANCELLED"
            if start and end and end > start and not cancelled:
                hours = Decimal((end - start).total_seconds()) / 3600
                if hours <= 24:
                    title = _unescape(event.get("SUMMARY", "")) or "Reunión"
                    meetings.append(Meeting(start.date(), to_quarters(hours), title[:200]))
            event = None
        elif event is not None and ":" in line:
            key, _, value = line.partition(":")
            name = key.split(";")[0].upper()
            if name in ("DTSTART", "DTEND"):
                event[name + "_KEY"] = key
            event[name] = value
        if len(meetings) > MAX_ROWS:
            raise ParseError(
                f"El calendario tiene más de {MAX_ROWS} reuniones; usa un rango menor."
            )
    if not meetings:
        raise ParseError("No se encontraron reuniones con hora de inicio y fin.")
    return sorted(meetings, key=lambda m: m.work_date)
