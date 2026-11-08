"""Rule-based extraction of time entries from a Spanish transcript. Pure functions, no I/O.

The rules always run; a local LLM may refine the result afterwards (see ``merge_llm``). Every
field carries its origin (``regla``, ``llm``) so the screen can say what was guessed.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from difflib import SequenceMatcher
from typing import Any

QUARTER = Decimal("0.25")
MAX_DESCRIPTION = 2000

MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}
NUMBER_WORDS = {
    "un": 1,
    "uno": 1,
    "una": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
}
STOP_WORDS = frozenset(
    [
        "de",
        "del",
        "la",
        "las",
        "el",
        "los",
        "un",
        "una",
        "unos",
        "unas",
        "y",
        "e",
        "o",
        "en",
        "con",
        "por",
        "para",
        "al",
        "se",
        "que",
        "su",
        "sus",
        "mi",
        "mis",
        "hoy",
        "ayer",
        "horas",
        "hora",
        "trabaje",
        "trabajo",
        "estuve",
        "hice",
    ]
)
LEADING_FILLER = re.compile(
    r"^(?:y|e|en|de|del|la|el|para|por|a|estuve|trabaje|trabajé|dedique|dediqué|hice|"
    r"durante|con)\b[\s,;:.-]*",
    re.IGNORECASE,
)

_NUM = r"(\d+(?:[.,]\d+)?|" + "|".join(NUMBER_WORDS) + r")"
_FRACTION = r"(media|cuarto|tres cuartos)"
_HOURS = re.compile(
    rf"\b{_NUM}\s*(?:horas?|hrs?|hs|h)\b(?:\s+y\s+{_FRACTION}\b)?"
    r"(?:\s*(?:y\s*)?(\d+)\s*(?:minutos?|min)\b)?"
)
_HALF_HOUR = re.compile(r"\bmedia\s+hora\b")
_BARE_HOUR_AND_HALF = re.compile(r"\bhora\s+y\s+media\b")
_QUARTERS = re.compile(r"\b(un|tres)\s+cuartos?\s+de\s+hora\b")
_MINUTES = re.compile(r"\b(\d+)\s*(?:minutos?|min)\b")
_SPLIT = re.compile(
    r"[.;\n]+|\b(?:y\s+luego|y\s+despues|y\s+tambien|luego|despues|ademas|tambien|"
    r"por\s+otro\s+lado)\b"
)
_AI_FLAG = re.compile(
    r"\b(?:(?:con|asistid[oa]\s+(?:por|con)|ayuda\s+de)\s+(?:la\s+)?"
    r"(?:ia|inteligencia\s+artificial|claude|copilot|chatgpt))\b"
)
_DAY_WORDS = re.compile(r"\b(anteayer|antes\s+de\s+ayer|ayer|hoy)\b")
_WEEKDAY = re.compile(
    r"\b(?:el\s+(" + "|".join(WEEKDAYS) + r")|(" + "|".join(WEEKDAYS) + r")\s+pasado)\b"
)
_DAY_MONTH = re.compile(
    r"\b(\d{1,2})\s+de\s+(" + "|".join(MONTHS) + r")(?:\s+(?:de|del)\s+(\d{4}))?\b"
)
_SLASH_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")


@dataclass(frozen=True)
class Option:
    id: int
    label: str
    code: str | None = None
    project_id: int | None = None  # only for phases


@dataclass(frozen=True)
class Candidates:
    projects: tuple[Option, ...] = ()
    phases: tuple[Option, ...] = ()
    buckets: tuple[Option, ...] = ()
    task_types: tuple[Option, ...] = ()
    applications: tuple[Option, ...] = ()


@dataclass
class DraftLine:
    work_date: date | None = None
    hours: Decimal | None = None
    project_id: int | None = None
    project_phase_id: int | None = None
    activity_bucket_id: int | None = None
    task_type_id: int | None = None
    application_id: int | None = None
    description: str | None = None
    ai_assisted: bool = False
    notes: list[str] = field(default_factory=list)
    origins: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "work_date": self.work_date.isoformat() if self.work_date else None,
            "hours": str(self.hours) if self.hours is not None else None,
            "project_id": self.project_id,
            "project_phase_id": self.project_phase_id,
            "activity_bucket_id": self.activity_bucket_id,
            "task_type_id": self.task_type_id,
            "application_id": self.application_id,
            "description": self.description,
            "ai_assisted": self.ai_assisted,
            "notes": self.notes,
            "origins": self.origins,
        }


_NOISE_TAGS = re.compile(
    r"\[[^\]]*\]|\((?:m[uú]sica|risas|aplausos|silencio|inaudible)\)", re.IGNORECASE
)


def clean_transcript(text: str) -> str:
    """Drop the tags Whisper writes for non-speech, such as ``[MÚSICA]`` or ``[BLANK_AUDIO]``."""
    return re.sub(r"\s+", " ", _NOISE_TAGS.sub(" ", text)).strip()


def fold(text: str) -> str:
    """Lowercase without accents, keeping the length so spans map back to the original."""
    out = []
    for char in text:
        base = unicodedata.normalize("NFD", char)[0].lower()
        out.append(base[0] if base else " ")
    return "".join(out)


def round_quarter(value: Decimal) -> Decimal:
    """Nearest quarter hour, at least one quarter for any positive amount."""
    if value <= 0:
        return Decimal(0)
    rounded = (value / QUARTER).quantize(Decimal(1), rounding=ROUND_HALF_UP) * QUARTER
    return max(rounded, QUARTER)


def _number(token: str) -> Decimal:
    if token in NUMBER_WORDS:
        return Decimal(NUMBER_WORDS[token])
    return Decimal(token.replace(",", "."))


_FRACTIONS = {"media": Decimal("0.5"), "cuarto": Decimal("0.25"), "tres cuartos": Decimal("0.75")}


def parse_hours(folded: str) -> tuple[Decimal | None, tuple[int, int] | None]:
    """Hours in a folded segment and the span the quantity occupies, if any."""
    if match := _HOURS.search(folded):
        value = _number(match.group(1))
        if match.group(2):
            value += _FRACTIONS[match.group(2)]
        if match.group(3):
            value += Decimal(match.group(3)) / 60
        return round_quarter(value), match.span()
    if match := _HALF_HOUR.search(folded):
        return Decimal("0.5"), match.span()
    if match := _BARE_HOUR_AND_HALF.search(folded):
        return Decimal("1.5"), match.span()
    if match := _QUARTERS.search(folded):
        return (Decimal("0.25") if match.group(1) == "un" else Decimal("0.75")), match.span()
    if match := _MINUTES.search(folded):
        return round_quarter(Decimal(match.group(1)) / 60), match.span()
    return None, None


def _last_weekday(today: date, weekday: int) -> date:
    return today - timedelta(days=(today.weekday() - weekday) % 7)


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_date(folded: str, today: date) -> tuple[date | None, tuple[int, int] | None]:
    """A date said in the segment (relative day, weekday, ``3 de noviembre`` or ``3/11``)."""
    if match := _DAY_WORDS.search(folded):
        word = re.sub(r"\s+", " ", match.group(1))
        offset = {"hoy": 0, "ayer": 1, "anteayer": 2, "antes de ayer": 2}[word]
        return today - timedelta(days=offset), match.span()
    if match := _WEEKDAY.search(folded):
        return _last_weekday(today, WEEKDAYS[match.group(1) or match.group(2)]), match.span()
    if match := _DAY_MONTH.search(folded):
        year = int(match.group(3)) if match.group(3) else today.year
        found = _safe_date(year, MONTHS[match.group(2)], int(match.group(1)))
        if found and not match.group(3) and found > today:
            found = _safe_date(year - 1, found.month, found.day)
        if found:
            return found, match.span()
    if match := _SLASH_DATE.search(folded):
        year = today.year
        if match.group(3):
            year = int(match.group(3))
            year += 2000 if year < 100 else 0
        found = _safe_date(year, int(match.group(2)), int(match.group(1)))
        if found and not match.group(3) and found > today:
            found = _safe_date(year - 1, found.month, found.day)
        if found:
            return found, match.span()
    return None, None


def _code_pattern(code: str) -> re.Pattern[str] | None:
    """A code like ``PJ-00001`` is also heard as ``pj 1``: separators and zeros are optional."""
    runs = re.findall(r"[a-z]+|\d+", fold(code))
    if not runs:
        return None
    parts = [f"0*{int(run)}" if run.isdigit() else re.escape(run) for run in runs]
    return re.compile(r"(?<![a-z0-9])" + r"[\s\-_]*".join(parts) + r"(?![a-z0-9])")


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text) if t not in STOP_WORDS]


def _token_hit(token: str, words: list[str]) -> bool:
    return any(
        word == token or (len(token) >= 5 and SequenceMatcher(None, token, word).ratio() >= 0.85)
        for word in words
    )


def _label_score(label: str, folded: str, words: list[str]) -> float:
    """1.0 for the whole label as words; else the share of its tokens heard (min 0.75)."""
    label_folded = fold(label).strip()
    if len(label_folded) >= 3 and re.search(
        r"(?<![a-z0-9])" + re.escape(label_folded) + r"(?![a-z0-9])", folded
    ):
        return 1.0
    tokens = [t for t in _tokens(label_folded) if len(t) >= 4]
    if not tokens:
        return 0.0
    share = sum(_token_hit(t, words) for t in tokens) / len(tokens)
    return share if share >= 0.75 else 0.0


@dataclass(frozen=True)
class Match:
    option: Option | None
    ambiguous: bool = False


def match_label(options: tuple[Option, ...], folded: str) -> Match:
    words = re.findall(r"[a-z0-9]+", folded)
    scored = [(_label_score(o.label, folded, words), o) for o in options]
    scored = [(s, o) for s, o in scored if s > 0]
    if not scored:
        return Match(None)
    best = max(s for s, _ in scored)
    top = [o for s, o in scored if s == best]
    if len(top) > 1:
        # A longer label that contains the shorter one wins ("QA" vs "QA regresión").
        top.sort(key=lambda o: len(o.label), reverse=True)
        if best == 1.0 and len(top[0].label) > len(top[1].label):
            return Match(top[0])
        return Match(None, ambiguous=True)
    return Match(top[0])


def match_code(options: tuple[Option, ...], folded: str) -> Match:
    found = [o for o in options if o.code and (p := _code_pattern(o.code)) and p.search(folded)]
    if len(found) == 1:
        return Match(found[0])
    return Match(None, ambiguous=len(found) > 1)


def split_segments(text: str) -> list[str]:
    """Split the transcript at sentence ends and connectors such as ``luego`` or ``además``."""
    folded = fold(text)
    pieces: list[str] = []
    last = 0
    for match in _SPLIT.finditer(folded):
        pieces.append(text[last : match.start()])
        last = match.end()
    pieces.append(text[last:])
    return [p.strip() for p in pieces if p.strip(" ,;:.-\n")]


def _description(original: str, spans: list[tuple[int, int]]) -> str | None:
    keep = list(original)
    for start, end in spans:
        for i in range(start, end):
            keep[i] = " "
    text = re.sub(r"\s+", " ", "".join(keep)).strip(" ,;:.-")
    while (cleaned := LEADING_FILLER.sub("", text, count=1).strip(" ,;:.-")) != text:
        text = cleaned
    return text[:MAX_DESCRIPTION] or None


def _line_from_group(
    group: str, today: date, current_date: date | None, candidates: Candidates
) -> tuple[DraftLine, date | None]:
    folded = fold(group)
    spans: list[tuple[int, int]] = []
    line = DraftLine()

    hours, span = parse_hours(folded)
    if span:
        spans.append(span)
    line.hours = hours
    if hours is not None:
        line.origins["hours"] = "regla"

    said, span = parse_date(folded, today)
    if span:
        spans.append(span)
    if said:
        current_date = said
    line.work_date = current_date or today
    line.origins["work_date"] = "regla"
    if line.work_date > today:
        line.notes.append("La fecha es futura; revísala.")

    if flag := _AI_FLAG.search(folded):
        line.ai_assisted = True
        spans.append(flag.span())

    project = match_code(candidates.projects, folded)
    if project.option is None and not project.ambiguous:
        project = match_label(candidates.projects, folded)
    if project.option:
        line.project_id = project.option.id
        line.origins["project_id"] = "regla"
        phases = tuple(p for p in candidates.phases if p.project_id == project.option.id)
        if found := match_label(phases, folded).option:
            line.project_phase_id = found.id
            line.origins["project_phase_id"] = "regla"
    elif project.ambiguous:
        line.notes.append("Varios proyectos coinciden; elige uno.")
    else:
        bucket = match_label(candidates.buckets, folded)
        if bucket.option:
            line.activity_bucket_id = bucket.option.id
            line.origins["activity_bucket_id"] = "regla"
        elif bucket.ambiguous:
            line.notes.append("Varias actividades coinciden; elige una.")

    if found := match_label(candidates.task_types, folded).option:
        line.task_type_id = found.id
        line.origins["task_type_id"] = "regla"
    app = match_code(candidates.applications, folded)
    if app.option is None and not app.ambiguous:
        app = match_label(candidates.applications, folded)
    if app.option:
        line.application_id = app.option.id
        line.origins["application_id"] = "regla"

    if line.project_id is None and line.activity_bucket_id is None and not line.notes:
        line.notes.append("Falta el proyecto o la actividad.")
    if line.hours is None:
        line.notes.append("No se entendió la cantidad de horas.")
    line.description = _description(group, spans)
    return line, current_date


def extract(text: str, today: date, candidates: Candidates) -> list[DraftLine]:
    """One draft line per segment that states an amount of time."""
    segments = split_segments(text)
    groups: list[str] = []
    prefix = ""
    for segment in segments:
        has_hours = parse_hours(fold(segment))[0] is not None
        if has_hours:
            groups.append((prefix + " " + segment).strip())
            prefix = ""
        elif groups:
            groups[-1] = f"{groups[-1]}, {segment}"
        else:
            prefix = (prefix + " " + segment).strip()
    if prefix:
        groups.append(prefix)

    lines: list[DraftLine] = []
    current: date | None = None
    for group in groups:
        line, current = _line_from_group(group, today, current, candidates)
        lines.append(line)
    return lines


def _valid_id(options: tuple[Option, ...], value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if any(o.id == value for o in options) else None


def _llm_line(raw: Any, today: date, candidates: Candidates) -> DraftLine | None:
    """Check one model line against the candidate lists; anything unknown becomes empty."""
    if not isinstance(raw, dict):
        return None
    line = DraftLine()
    try:
        line.hours = round_quarter(Decimal(str(raw.get("hours"))))
    except ArithmeticError:
        return None
    if line.hours <= 0 or line.hours > 24:
        return None
    try:
        line.work_date = date.fromisoformat(str(raw.get("date")))
    except ValueError:
        line.work_date = today
    line.project_id = _valid_id(candidates.projects, raw.get("project_id"))
    if line.project_id is None:
        line.activity_bucket_id = _valid_id(candidates.buckets, raw.get("bucket_id"))
    else:
        phases = tuple(p for p in candidates.phases if p.project_id == line.project_id)
        line.project_phase_id = _valid_id(phases, raw.get("phase_id"))
    line.task_type_id = _valid_id(candidates.task_types, raw.get("task_type_id"))
    line.application_id = _valid_id(candidates.applications, raw.get("application_id"))
    description = raw.get("description")
    if isinstance(description, str) and description.strip():
        line.description = description.strip()[:MAX_DESCRIPTION]
    line.ai_assisted = raw.get("ai_assisted") is True
    return line


_FIELDS = (
    "work_date",
    "project_id",
    "project_phase_id",
    "activity_bucket_id",
    "task_type_id",
    "application_id",
    "description",
)


def merge_llm(
    rules: list[DraftLine], raw_lines: Any, today: date, candidates: Candidates
) -> list[DraftLine]:
    """Prefer valid model values field by field; keep the rules' value where the model has none.

    If the model proposes a different number of lines, its (validated) lines replace the rules'.
    """
    proposed = [
        line
        for raw in (raw_lines if isinstance(raw_lines, list) else [])
        if (line := _llm_line(raw, today, candidates)) is not None
    ]
    if not proposed:
        return rules
    for line in proposed:
        for name in _FIELDS:
            if getattr(line, name) is not None:
                line.origins[name] = "llm"
        line.origins["hours"] = "llm"
    if len(proposed) != len(rules):
        for line in proposed:
            if line.project_id is None and line.activity_bucket_id is None:
                line.notes.append("Falta el proyecto o la actividad.")
        proposed[0].notes.append("El modelo local propuso otro número de líneas que las reglas.")
        return proposed
    merged: list[DraftLine] = []
    for rule, llm in zip(rules, proposed, strict=True):
        # The model's target replaces the rules' target as a whole (only one may be set).
        if llm.project_id is None and llm.activity_bucket_id is None:
            llm.project_id, llm.activity_bucket_id = rule.project_id, rule.activity_bucket_id
            llm.project_phase_id = llm.project_phase_id or rule.project_phase_id
            for name in ("project_id", "activity_bucket_id"):
                if getattr(llm, name) is not None:
                    llm.origins[name] = "regla"
        for name in _FIELDS:
            if getattr(llm, name) is None and getattr(rule, name) is not None:
                setattr(llm, name, getattr(rule, name))
                llm.origins[name] = rule.origins.get(name, "regla")
        llm.ai_assisted = llm.ai_assisted or rule.ai_assisted
        llm.notes = list(rule.notes)
        if llm.project_id is not None or llm.activity_bucket_id is not None:
            llm.notes = [n for n in llm.notes if not n.startswith(("Falta", "Varios", "Varias"))]
        merged.append(llm)
    return merged
