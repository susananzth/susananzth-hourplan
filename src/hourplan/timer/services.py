"""Stopwatch: persistent start, single focus, interruption and idle time.

Every function takes ``now`` so the rules can be tested without waiting.
"""

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.db import transaction
from hourplan.shared.labels import label
from hourplan.timer.models import (
    OPEN_STATUSES,
    TimerPreference,
    TimerSession,
    TimerStatus,
)
from hourplan.timesheet import services as timesheet
from hourplan.timesheet.models import EntrySource, TimeEntry
from hourplan.timesheet.services import RowKey

HEARTBEAT_SECONDS = 60
# Without a heartbeat for this long the timer is paused at the last one.
STALE_SECONDS = 150
DEFAULT_IDLE_MINUTES = 5
MIN_SECONDS = 60
QUARTER = Decimal("0.25")

IDLE_CHOICES = {
    "meeting": "Reunión o llamada presencial",
    "assist": "Asistencia a un compañero",
    "offscreen": "Trabajo sin pantalla",
    "break": "Descanso",
}


class TimerError(ValueError):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


def hours_for(seconds: int) -> Decimal:
    """Quarter hours nearest to the elapsed time, at most 24."""
    quarters = (Decimal(seconds) / 3600 / QUARTER).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return min(quarters * QUARTER, Decimal(24))


def elapsed_seconds(timer: TimerSession, now: datetime) -> int:
    total = timer.accumulated_seconds
    if timer.status == TimerStatus.RUNNING and timer.segment_started_at:
        end = min(now, timer.last_beat_at + timedelta(seconds=STALE_SECONDS))
        total += max(0, int((end - timer.segment_started_at).total_seconds()))
    return total


def _pause_at(timer: TimerSession, moment: datetime) -> None:
    if timer.status != TimerStatus.RUNNING or timer.segment_started_at is None:
        return
    timer.accumulated_seconds += max(0, int((moment - timer.segment_started_at).total_seconds()))
    timer.segment_started_at = None
    timer.status = TimerStatus.PAUSED.value


def open_timers(session: Session, resource_id: int, now: datetime) -> list[TimerSession]:
    """Open timers, running first. A running timer with no recent heartbeat is paused here.

    The change is only staged; ``settle`` or the caller's own transaction stores it.
    """
    found = list(
        session.scalars(
            select(TimerSession)
            .where(TimerSession.resource_id == resource_id, TimerSession.status.in_(OPEN_STATUSES))
            .order_by(TimerSession.started_at)
        )
    )
    for timer in found:
        if timer.status == TimerStatus.RUNNING and now - timer.last_beat_at > timedelta(
            seconds=STALE_SECONDS
        ):
            _pause_at(timer, timer.last_beat_at)
    return sorted(found, key=lambda t: t.status != TimerStatus.RUNNING)


def settle(session: Session, resource_id: int, now: datetime) -> None:
    """Store the pause of timers whose heartbeats stopped."""
    with transaction(session):
        open_timers(session, resource_id, now)


def running(session: Session, resource_id: int, now: datetime) -> TimerSession | None:
    return next(
        (t for t in open_timers(session, resource_id, now) if t.status == TimerStatus.RUNNING),
        None,
    )


def _get(session: Session, resource_id: int, timer_id: int, now: datetime) -> TimerSession:
    """The open timer; the action itself shows the browser is alive, so it counts as a beat."""
    timer = session.get(TimerSession, timer_id)
    if timer is None or timer.resource_id != resource_id or timer.status not in OPEN_STATUSES:
        raise TimerError("No se encontró ese cronómetro.")
    open_timers(session, resource_id, now)
    if timer.status == TimerStatus.RUNNING:
        timer.last_beat_at = now
    return timer


def _pause_running(session: Session, resource_id: int, now: datetime) -> None:
    for timer in open_timers(session, resource_id, now):
        if timer.status == TimerStatus.RUNNING:
            _pause_at(timer, min(now, timer.last_beat_at + timedelta(seconds=STALE_SECONDS)))
    session.flush()


def _resume(timer: TimerSession, now: datetime) -> None:
    timer.status = TimerStatus.RUNNING.value
    timer.segment_started_at = now
    timer.last_beat_at = now


def start(
    session: Session,
    resource_id: int,
    key: RowKey,
    now: datetime,
    *,
    description: str | None = None,
    ai_assisted: bool = False,
) -> TimerSession:
    """Start on a task; the running timer is paused. A paused one on the same task resumes."""
    if key.project_id is None and key.activity_bucket_id is None:
        raise TimerError(
            f"Indica {label('project_id').lower()} o {label('activity_bucket_id').lower()}."
        )
    timesheet.EntryData(work_date=now.date(), hours=QUARTER, **asdict(key))  # validates the key
    with transaction(session):
        _pause_running(session, resource_id, now)
        same = [
            t
            for t in open_timers(session, resource_id, now)
            if not t.is_interruption and asdict(_key(t)) == asdict(key)
        ]
        if same:
            timer = same[0]
            _resume(timer, now)
            return timer
        timer = TimerSession(
            resource_id=resource_id,
            status=TimerStatus.RUNNING.value,
            started_at=now,
            segment_started_at=now,
            last_beat_at=now,
            description=(description or "").strip() or None,
            ai_assisted=ai_assisted,
            **asdict(key),
        )
        session.add(timer)
    return timer


def _key(timer: TimerSession) -> RowKey:
    return RowKey(
        project_id=timer.project_id,
        activity_bucket_id=timer.activity_bucket_id,
        project_phase_id=timer.project_phase_id,
        task_type_id=timer.task_type_id,
    )


def interrupt(session: Session, resource_id: int, now: datetime) -> TimerSession:
    """Pause the running task and open an unclassified timer; it is classified when it ends."""
    with transaction(session):
        current = running(session, resource_id, now)
        _pause_running(session, resource_id, now)
        timer = TimerSession(
            resource_id=resource_id,
            status=TimerStatus.RUNNING.value,
            started_at=now,
            segment_started_at=now,
            last_beat_at=now,
            is_interruption=True,
            interrupted_id=current.id if current else None,
        )
        session.add(timer)
    return timer


def pause(session: Session, resource_id: int, timer_id: int, now: datetime) -> None:
    with transaction(session):
        timer = _get(session, resource_id, timer_id, now)
        _pause_at(timer, min(now, timer.last_beat_at + timedelta(seconds=STALE_SECONDS)))


def resume(session: Session, resource_id: int, timer_id: int, now: datetime) -> None:
    with transaction(session):
        timer = _get(session, resource_id, timer_id, now)
        if timer.status == TimerStatus.RUNNING:
            return
        _pause_running(session, resource_id, now)
        _resume(timer, now)


def heartbeat(session: Session, resource_id: int, now: datetime) -> TimerSession | None:
    """Record that the browser is still open. Returns the running timer, if any."""
    timer = running(session, resource_id, now)
    if timer is not None:
        with transaction(session):
            timer.last_beat_at = now
    return timer


def discard(session: Session, resource_id: int, timer_id: int, now: datetime) -> None:
    with transaction(session):
        timer = _get(session, resource_id, timer_id, now)
        was_running = timer.status == TimerStatus.RUNNING
        timer.status = TimerStatus.DISCARDED.value
        timer.segment_started_at = None
        timer.ended_at = now
        _back_to_interrupted(session, timer, now, was_running)


def _back_to_interrupted(
    session: Session, timer: TimerSession, now: datetime, was_running: bool
) -> None:
    """When an interruption ends, the task it paused continues."""
    if not (timer.is_interruption and was_running and timer.interrupted_id):
        return
    parent = session.get(TimerSession, timer.interrupted_id)
    if parent is not None and parent.status == TimerStatus.PAUSED:
        session.flush()
        _resume(parent, now)


def stop(
    session: Session,
    resource_id: int,
    timer_id: int,
    now: datetime,
    *,
    key: RowKey | None = None,
    description: str | None = None,
    ai_assisted: bool | None = None,
    actor_id: int | None,
) -> TimeEntry:
    """Close the timer into a time entry of the rounded quarter hours."""
    timer = _get(session, resource_id, timer_id, now)
    seconds = elapsed_seconds(timer, now)
    if seconds < MIN_SECONDS:
        raise TimerError("Lleva menos de un minuto: descártalo en lugar de guardarlo.")
    target = key if key is not None else _key(timer)
    if target.project_id is None and target.activity_bucket_id is None:
        raise TimerError("Clasifica el tiempo: indica un proyecto o una actividad.")
    data = timesheet.EntryData(
        work_date=(timer.started_at.astimezone().date()),
        hours=max(hours_for(seconds), QUARTER),
        description=description if description is not None else timer.description,
        ai_assisted=timer.ai_assisted if ai_assisted is None else ai_assisted,
        **asdict(target),
    )
    # create_entry commits the session, which stores the timer change in the same transaction.
    was_running = timer.status == TimerStatus.RUNNING
    try:
        timer.status = TimerStatus.STOPPED.value
        timer.segment_started_at = None
        timer.ended_at = now
        entry, _ = timesheet.create_entry(
            session, resource_id, data, actor_id=actor_id, source=EntrySource.TIMER
        )
        timer.time_entry_id = entry.id
        _back_to_interrupted(session, timer, now, was_running)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    return entry


def apply_idle(
    session: Session, resource_id: int, seconds: int, choice: str, now: datetime
) -> TimerSession | None:
    """After an inactivity: rest is discarded from the timer, anything else is kept and noted."""
    if choice not in IDLE_CHOICES:
        raise TimerError("Opción de inactividad no válida.")
    timer = running(session, resource_id, now)
    if timer is None:
        return None
    with transaction(session):
        if choice == "break":
            _pause_at(timer, now)
            timer.accumulated_seconds = max(0, timer.accumulated_seconds - max(0, seconds))
            _resume(timer, now)
        else:
            note = IDLE_CHOICES[choice]
            if note not in (timer.description or ""):
                timer.description = (
                    f"{timer.description + ' · ' if timer.description else ''}{note}"
                )
    return timer


def idle_minutes(session: Session, resource_id: int) -> int:
    pref = session.scalars(
        select(TimerPreference).where(TimerPreference.resource_id == resource_id)
    ).first()
    return pref.idle_minutes if pref else DEFAULT_IDLE_MINUTES


def set_idle_minutes(session: Session, resource_id: int, minutes: int) -> None:
    if not 1 <= minutes <= 120:
        raise TimerError("La inactividad debe estar entre 1 y 120 minutos.")
    with transaction(session):
        pref = session.scalars(
            select(TimerPreference).where(TimerPreference.resource_id == resource_id)
        ).first()
        if pref is None:
            session.add(TimerPreference(resource_id=resource_id, idle_minutes=minutes))
        else:
            pref.idle_minutes = minutes
