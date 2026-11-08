"""Dictation queue, drafts and confirmation. The audio is erased as soon as it is transcribed."""

import io
import wave
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from time import monotonic
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from hourplan.catalogs.models import Application, CatalogItem, CatalogKind
from hourplan.db import transaction
from hourplan.projects.models import Project, ProjectPhase
from hourplan.shared.labels import validation_message
from hourplan.timesheet import services as timesheet
from hourplan.timesheet.models import EntrySource
from hourplan.timesheet.services import EntryData
from hourplan.voice import extract
from hourplan.voice.clients import EngineError, LlmClient, SttClient
from hourplan.voice.extract import Candidates, DraftLine, Option
from hourplan.voice.models import DictationStatus, VoiceDictation

SAMPLE_RATE = 16_000
MAX_ATTEMPTS = 3
STALE_AFTER = timedelta(minutes=15)
KEEP_UNANSWERED = timedelta(days=7)
MAX_LINES = 12


class VoiceError(Exception):
    """A message that is safe to show to the person."""


@dataclass(frozen=True)
class Engines:
    stt: SttClient
    llm: LlmClient | None = None


def utcnow() -> datetime:
    return datetime.now(UTC)


def wav_duration(data: bytes, max_seconds: float) -> float:
    """Seconds of a 16 kHz mono 16-bit WAV; anything else is refused."""
    try:
        with wave.open(io.BytesIO(data)) as reader:
            ok = (
                reader.getnchannels() == 1
                and reader.getsampwidth() == 2
                and reader.getframerate() == SAMPLE_RATE
            )
            seconds = reader.getnframes() / SAMPLE_RATE
    except (wave.Error, EOFError) as err:
        raise VoiceError("El audio no tiene un formato válido.") from err
    if not ok:
        raise VoiceError("El audio debe ser mono de 16 kHz y 16 bits.")
    if seconds < 0.5:
        raise VoiceError("El audio es demasiado corto.")
    if seconds > max_seconds + 5:
        raise VoiceError(f"El dictado puede durar hasta {int(max_seconds // 60)} minutos.")
    return float(round(seconds, 2))


def create_dictation(
    session: Session, resource_id: int, wav: bytes, *, actor_id: int | None, max_seconds: float
) -> VoiceDictation:
    seconds = wav_duration(wav, max_seconds)
    with transaction(session):
        item = VoiceDictation(
            resource_id=resource_id, created_by_id=actor_id, audio=wav, duration_s=seconds
        )
        session.add(item)
        session.flush()
    return item


def get_for(session: Session, dictation_id: int, resource_id: int) -> VoiceDictation:
    item = session.get(VoiceDictation, dictation_id)
    if item is None or item.resource_id != resource_id:
        raise VoiceError("No se encontró el dictado.")
    return item


def candidates(session: Session) -> Candidates:
    """Open projects and active catalogs; this is also what the draft form offers."""
    projects = session.scalars(
        select(Project).where(Project.closed_on.is_(None)).order_by(Project.code)
    ).all()
    phases = session.scalars(
        select(ProjectPhase)
        .where(ProjectPhase.project_id.in_([p.id for p in projects]))
        .order_by(ProjectPhase.project_id, ProjectPhase.position)
    ).all()

    def items(kind: CatalogKind) -> tuple[Option, ...]:
        rows = session.scalars(
            select(CatalogItem)
            .where(CatalogItem.kind == kind.value, CatalogItem.is_active.is_(True))
            .order_by(CatalogItem.name)
        )
        return tuple(Option(r.id, r.name) for r in rows)

    apps = session.scalars(
        select(Application).where(Application.is_active.is_(True)).order_by(Application.name)
    )
    return Candidates(
        projects=tuple(Option(p.id, p.title, p.code) for p in projects),
        phases=tuple(Option(f.id, f.name, project_id=f.project_id) for f in phases),
        buckets=items(CatalogKind.ACTIVITY_BUCKET),
        task_types=items(CatalogKind.TASK_TYPE),
        applications=tuple(Option(a.id, a.name, a.code) for a in apps),
    )


def whisper_prompt(cands: Candidates, limit: int = 60) -> str:
    """Codes and names bias the transcription toward the vocabulary of this team."""
    words = [o.code or "" for o in cands.projects[:limit]]
    words += [o.label for o in cands.projects[:limit]]
    words += [o.code or o.label for o in cands.applications[:limit]]
    return ", ".join(w for w in words if w)[:800]


def claim_next(session: Session, now: datetime) -> int | None:
    """Take the oldest queued dictation (``SKIP LOCKED`` lets several workers coexist)."""
    with transaction(session):
        item = session.scalars(
            select(VoiceDictation)
            .where(VoiceDictation.status == DictationStatus.QUEUED.value)
            .order_by(VoiceDictation.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if item is None:
            return None
        item.status = DictationStatus.PROCESSING.value
        item.attempts += 1
        item.updated_at = now
        return item.id


def requeue_stale(session: Session, now: datetime) -> int:
    """A worker that died leaves its dictation ``processing``; give it back to the queue."""
    with transaction(session):
        result = session.execute(
            update(VoiceDictation)
            .where(
                VoiceDictation.status == DictationStatus.PROCESSING.value,
                VoiceDictation.updated_at < now - STALE_AFTER,
            )
            .values(status=DictationStatus.QUEUED.value, updated_at=now)
        )
        return int(getattr(result, "rowcount", 0))


def _fail(session: Session, dictation_id: int, message: str) -> None:
    with transaction(session):
        item = session.get(VoiceDictation, dictation_id)
        if item is None:
            return
        item.error = message[:300]
        if item.attempts < MAX_ATTEMPTS:
            item.status = DictationStatus.QUEUED.value
        else:
            item.status = DictationStatus.FAILED.value
            item.audio = None


def process(
    session: Session,
    dictation_id: int,
    engines: Engines,
    today: date,
    *,
    clock: Callable[[], float] = monotonic,
) -> None:
    """Transcribe, extract and store the draft; the HTTP calls run outside any transaction."""
    item = session.get(VoiceDictation, dictation_id)
    if item is None or item.status != DictationStatus.PROCESSING.value or item.audio is None:
        return
    wav = item.audio
    cands = candidates(session)
    prompt = whisper_prompt(cands)
    session.commit()

    started = clock()
    try:
        transcript = engines.stt.transcribe(wav, prompt=prompt)
    except EngineError as err:
        _fail(session, dictation_id, str(err))
        return
    stt_seconds = clock() - started

    lines = extract.extract(transcript, today, cands)
    llm_seconds: float | None = None
    llm_model: str | None = None
    warning: str | None = None
    if engines.llm is not None and transcript:
        started = clock()
        try:
            raw = engines.llm.extract(transcript, today, cands)
            lines = extract.merge_llm(lines, raw, today, cands)
            llm_model = engines.llm.model
        except EngineError:
            warning = "El modelo local no respondió; el borrador sale solo de las reglas."
        llm_seconds = clock() - started
    if not lines:
        lines = [DraftLine(work_date=today, notes=["No se entendió ninguna línea de horas."])]
    if warning:
        lines[0].notes.append(warning)

    with transaction(session):
        item = session.get(VoiceDictation, dictation_id)
        if item is None:
            return
        item.audio = None
        item.transcript = transcript
        item.draft = [line.to_json() for line in lines[:MAX_LINES]]
        item.status = DictationStatus.READY.value
        item.error = None
        item.stt_model = engines.stt.model
        item.llm_model = llm_model
        item.stt_seconds = round(stt_seconds, 2)
        item.llm_seconds = round(llm_seconds, 2) if llm_seconds is not None else None


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", ".").strip())
    except InvalidOperation as err:
        raise VoiceError("Las horas no son un número.") from err


def build_entries(session: Session, rows: list[dict[str, Any]]) -> list[EntryData]:
    """Validate every row before anything is saved; the message says which row failed."""
    entries: list[EntryData] = []
    for number, row in enumerate(rows, start=1):
        try:
            data = EntryData(
                work_date=date.fromisoformat(str(row.get("work_date"))),
                hours=_decimal(str(row.get("hours", ""))),
                project_id=row.get("project_id"),
                activity_bucket_id=row.get("activity_bucket_id"),
                project_phase_id=row.get("project_phase_id"),
                task_type_id=row.get("task_type_id"),
                application_id=row.get("application_id"),
                description=row.get("description"),
                ai_assisted=bool(row.get("ai_assisted")),
            )
        except (ValidationError, ValueError, VoiceError) as err:
            message = validation_message(err) if isinstance(err, ValidationError) else str(err)
            raise VoiceError(f"Línea {number}: {message}") from err
        if data.project_phase_id is not None:
            phase = session.get(ProjectPhase, data.project_phase_id)
            if phase is None or phase.project_id != data.project_id:
                data = data.model_copy(update={"project_phase_id": None})
        entries.append(data)
    if not entries:
        raise VoiceError("Marca al menos una línea para guardar.")
    return entries


def confirm(
    session: Session,
    dictation_id: int,
    resource_id: int,
    rows: list[dict[str, Any]],
    *,
    actor_id: int | None,
) -> int:
    """Save the confirmed lines as ``voice`` entries and clear the text of the dictation."""
    item = session.scalars(
        select(VoiceDictation)
        .where(VoiceDictation.id == dictation_id, VoiceDictation.resource_id == resource_id)
        .with_for_update()
    ).first()
    if item is None:
        raise VoiceError("No se encontró el dictado.")
    if item.status != DictationStatus.READY.value:
        raise VoiceError("Este dictado ya no está listo para confirmar.")
    entries = build_entries(session, rows)
    item.status = DictationStatus.CONFIRMED.value
    item.transcript = None
    item.draft = None
    # ``create_entry`` commits, which also stores the new status of the dictation.
    for data in entries:
        try:
            timesheet.create_entry(
                session, resource_id, data, actor_id=actor_id, source=EntrySource.VOICE
            )
        except timesheet.EntryError as err:
            session.rollback()
            raise VoiceError(str(err)) from err
    return len(entries)


def discard(session: Session, dictation_id: int, resource_id: int) -> None:
    item = get_for(session, dictation_id, resource_id)
    if item.status in (DictationStatus.CONFIRMED.value, DictationStatus.DISCARDED.value):
        return
    with transaction(session):
        item.status = DictationStatus.DISCARDED.value
        item.audio = None
        item.transcript = None
        item.draft = None


def purge(session: Session, now: datetime) -> int:
    """Blank the text of dictations nobody answered; delete the empty rows after 90 days."""
    cutoff = now - KEEP_UNANSWERED
    with transaction(session):
        result = session.execute(
            update(VoiceDictation)
            .where(
                VoiceDictation.created_at < cutoff,
                VoiceDictation.status.in_(
                    [DictationStatus.READY.value, DictationStatus.FAILED.value]
                ),
            )
            .values(
                status=DictationStatus.DISCARDED.value,
                audio=None,
                transcript=None,
                draft=None,
            )
        )
        session.execute(
            delete(VoiceDictation).where(
                VoiceDictation.created_at < now - timedelta(days=90),
                VoiceDictation.status.in_(
                    [DictationStatus.CONFIRMED.value, DictationStatus.DISCARDED.value]
                ),
            )
        )
        return int(getattr(result, "rowcount", 0))
