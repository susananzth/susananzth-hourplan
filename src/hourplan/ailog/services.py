"""Turn a parsed log or calendar into an editable draft and save the confirmed lines."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from hourplan.ailog.models import AccessToken, AiDraft, DraftStatus
from hourplan.ailog.parsers import LogRow, Meeting, to_quarters
from hourplan.audit.models import AuditAction
from hourplan.audit.services import record
from hourplan.auth.models import User
from hourplan.auth.roles import Role
from hourplan.catalogs.models import Application, CatalogItem, CatalogKind
from hourplan.projects.models import Project
from hourplan.resources.models import Resource
from hourplan.shared.labels import validation_message
from hourplan.shared.translate import _
from hourplan.timesheet import services as timesheet
from hourplan.timesheet.models import EntrySource, TimeEntry
from hourplan.timesheet.services import EntryData

MAX_LINES = 200
MAX_TOKENS = 5
MAX_PROPOSED = 20
MAX_PENDING = 200
TOKEN_PREFIX = "hp_"  # noqa: S105 - public marker, not a secret
WRITERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER)


class AilogError(Exception):
    """A message for the person, in Spanish."""


@dataclass(frozen=True)
class Option:
    id: int
    label: str
    code: str | None = None


@dataclass(frozen=True)
class Choices:
    projects: tuple[Option, ...]
    buckets: tuple[Option, ...]
    task_types: tuple[Option, ...]
    applications: tuple[Option, ...]


def choices(session: Session) -> Choices:
    projects = session.scalars(
        select(Project).where(Project.closed_on.is_(None)).order_by(Project.code)
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
    return Choices(
        projects=tuple(Option(p.id, p.title, p.code) for p in projects),
        buckets=items(CatalogKind.ACTIVITY_BUCKET),
        task_types=items(CatalogKind.TASK_TYPE),
        applications=tuple(Option(a.id, a.name, a.code) for a in apps),
    )


def _already_saved(
    session: Session, resource_id: int, work_date: date, hours: Decimal, text: str | None
) -> bool:
    return (
        session.scalar(
            select(func.count())
            .select_from(TimeEntry)
            .where(
                TimeEntry.resource_id == resource_id,
                TimeEntry.work_date == work_date,
                TimeEntry.hours == hours,
                TimeEntry.description == text,
                TimeEntry.source.in_([EntrySource.AI_LOG.value, EntrySource.IMPORT.value]),
            )
        )
        or 0
    ) > 0


def _line(
    work_date: date, hours: Decimal, description: str | None, ai_assisted: bool
) -> dict[str, Any]:
    return {
        "use": True,
        "work_date": work_date.isoformat(),
        "hours": str(hours),
        "project_id": None,
        "activity_bucket_id": None,
        "project_phase_id": None,
        "task_type_id": None,
        "application_id": None,
        "description": description,
        "ai_assisted": ai_assisted,
        "notes": [],
    }


def draft_from_log(session: Session, resource_id: int, rows: list[LogRow]) -> list[dict[str, Any]]:
    """Resolve each code against open projects and tickets; unknown codes stay for the person."""
    by_code: dict[str, list[Project]] = {}
    for project in session.scalars(select(Project).where(Project.closed_on.is_(None))):
        by_code.setdefault(project.code.lower(), []).append(project)
    lines: list[dict[str, Any]] = []
    for row in rows:
        line = _line(row.work_date, row.hours, row.description, True)
        found = by_code.get(row.code.lower(), [])
        if len(found) == 1:
            line["project_id"] = found[0].id
        elif found:
            line["project_id"] = found[0].id
            line["notes"].append(
                _("El código %s existe como proyecto y como ticket; revisa.") % (row.code,)
            )
        else:
            line["use"] = False
            line["notes"].append(
                _("No se encontró el código «%s»: elige el proyecto.") % (row.code,)
            )
        if _already_saved(session, resource_id, row.work_date, row.hours, row.description):
            line["use"] = False
            line["notes"].append(_("Parece ya guardada antes; márcala si quieres repetirla."))
        lines.append(line)
    return lines


def draft_from_calendar(
    session: Session, resource_id: int, meetings: list[Meeting]
) -> list[dict[str, Any]]:
    """Meetings go to the meeting bucket when there is one; the person picks the project."""
    bucket = next((b for b in choices(session).buckets if b.label.lower().startswith("reun")), None)
    lines: list[dict[str, Any]] = []
    for meeting in meetings:
        line = _line(meeting.work_date, meeting.hours, meeting.title, False)
        if bucket is not None:
            line["activity_bucket_id"] = bucket.id
        else:
            line["notes"].append(_("No hay actividad «Reunión»: elige a dónde van estas horas."))
        if _already_saved(session, resource_id, meeting.work_date, meeting.hours, meeting.title):
            line["use"] = False
            line["notes"].append(_("Parece ya guardada antes; márcala si quieres repetirla."))
        lines.append(line)
    return lines


def _decimal(raw: str) -> Decimal:
    try:
        return Decimal(raw.replace(",", ".").strip())
    except InvalidOperation as err:
        raise AilogError("Las horas no son un número.") from err


def build_entries(rows: list[dict[str, Any]]) -> list[EntryData]:
    """Validate every chosen line before anything is saved; the message says which one failed."""
    entries: list[EntryData] = []
    for number, row in enumerate(rows, start=1):
        try:
            entries.append(
                EntryData(
                    work_date=date.fromisoformat(str(row.get("work_date"))),
                    hours=_decimal(str(row.get("hours", ""))),
                    project_id=row.get("project_id"),
                    activity_bucket_id=row.get("activity_bucket_id"),
                    task_type_id=row.get("task_type_id"),
                    application_id=row.get("application_id"),
                    description=row.get("description"),
                    ai_assisted=bool(row.get("ai_assisted")),
                )
            )
        except (ValidationError, ValueError, AilogError) as err:
            message = validation_message(err) if isinstance(err, ValidationError) else str(err)
            raise AilogError(
                _("Línea %s: %s")
                % (
                    number,
                    message,
                )
            ) from err
    if not entries:
        raise AilogError("Marca al menos una línea para guardar.")
    return entries


def confirm(
    session: Session,
    resource_id: int,
    rows: list[dict[str, Any]],
    *,
    source: EntrySource,
    actor_id: int | None,
    settle: tuple[list[int], list[int]] = ([], []),
) -> int:
    """``settle`` = (draft ids confirmed, draft ids discarded) when the lines came from MCP."""
    entries = build_entries(rows)
    confirmed, discarded = settle
    for ids, status in ((confirmed, DraftStatus.CONFIRMED), (discarded, DraftStatus.DISCARDED)):
        if ids:
            session.execute(
                update(AiDraft)
                .where(
                    AiDraft.id.in_(ids),
                    AiDraft.resource_id == resource_id,
                    AiDraft.status == DraftStatus.PENDING.value,
                )
                .values(status=status.value)
            )
    for number, data in enumerate(entries, start=1):
        try:
            timesheet.create_entry(session, resource_id, data, actor_id=actor_id, source=source)
        except timesheet.EntryError as err:
            session.rollback()
            raise AilogError(
                _("Línea %s: %s")
                % (
                    number,
                    err,
                )
            ) from err
    return len(entries)


# ------------------------------------------------------------------------- tokens


def _hash(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def active_tokens(session: Session, user_id: int, now: datetime) -> list[AccessToken]:
    return list(
        session.scalars(
            select(AccessToken)
            .where(
                AccessToken.user_id == user_id,
                AccessToken.revoked_at.is_(None),
                AccessToken.expires_at > now,
            )
            .order_by(AccessToken.created_at.desc())
        )
    )


def create_token(
    session: Session, user_id: int, name: str, *, days: int, now: datetime
) -> tuple[AccessToken, str]:
    """Return the token and its plain value, which is never stored nor shown again."""
    name = name.strip()
    if not name or len(name) > 60:
        raise AilogError("Ponle un nombre de 1 a 60 caracteres (por ejemplo, «Portátil»).")
    if len(active_tokens(session, user_id, now)) >= MAX_TOKENS:
        raise AilogError(_("Ya tienes %s tokens activos; revoca uno antes.") % (MAX_TOKENS,))
    plain = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = AccessToken(
        user_id=user_id,
        name=name,
        token_hash=_hash(plain),
        prefix=plain[:10],
        expires_at=now + timedelta(days=days),
    )
    session.add(token)
    session.flush()
    record(
        session,
        action=AuditAction.CREATE,
        entity_type="access_token",
        entity_id=token.id,
        actor_id=user_id,
        changes={"name": name, "expires_at": token.expires_at},
        subject_user_id=user_id,
    )
    session.commit()
    return token, plain


def revoke_token(session: Session, user_id: int, token_id: int, now: datetime) -> None:
    token = session.get(AccessToken, token_id)
    if token is None or token.user_id != user_id or token.revoked_at is not None:
        raise AilogError("No se encontró el token.")
    token.revoked_at = now
    record(
        session,
        action=AuditAction.UPDATE,
        entity_type="access_token",
        entity_id=token.id,
        actor_id=user_id,
        changes={"revoked_at": now},
        subject_user_id=user_id,
    )
    session.commit()


@dataclass(frozen=True)
class Caller:
    token_id: int
    user_id: int
    resource_id: int


def authenticate(session: Session, plain: str, now: datetime) -> Caller | None:
    """A live token of an active writer with an active resource; anything else is refused."""
    if not plain.startswith(TOKEN_PREFIX) or len(plain) > 100:
        return None
    token = session.scalars(
        select(AccessToken).where(AccessToken.token_hash == _hash(plain))
    ).first()
    if token is None or token.revoked_at is not None or token.expires_at <= now:
        return None
    user = session.get(User, token.user_id)
    if user is None or not user.is_active or not user.has_role(*WRITERS):
        return None
    resource = session.scalars(
        select(Resource).where(Resource.user_id == user.id, Resource.is_active)
    ).first()
    if resource is None:
        return None
    if token.last_used_at is None or now - token.last_used_at > timedelta(minutes=5):
        token.last_used_at = now
        session.commit()
    return Caller(token.id, user.id, resource.id)


# ------------------------------------------------------------------- MCP drafts


def search_projects(session: Session, text: str, limit: int = 20) -> list[Project]:
    query = select(Project).where(Project.closed_on.is_(None))
    text = text.strip()[:60]
    if text:
        pattern = "%" + text.replace("\\", "").replace("%", "").replace("_", "") + "%"
        query = query.where(or_(Project.code.ilike(pattern), Project.title.ilike(pattern)))
    return list(session.scalars(query.order_by(Project.code).limit(limit)))


def _proposed(number: int, raw: Any) -> AiDraft:
    if not isinstance(raw, dict):
        raise AilogError(_("Línea %s: debe ser un objeto.") % (number,))
    try:
        work_date = date.fromisoformat(str(raw.get("fecha", "")))
    except ValueError as err:
        raise AilogError(_("Línea %s: la fecha debe ser AAAA-MM-DD.") % (number,)) from err
    code = str(raw.get("codigo", "")).strip()
    if not code or len(code) > 40:
        raise AilogError(_("Línea %s: falta el código del proyecto o ticket.") % (number,))
    minutes = raw.get("minutos")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 1440:
        raise AilogError(_("Línea %s: los minutos deben ser un entero entre 1 y 1440.") % (number,))
    description = str(raw.get("descripcion") or "").strip()[:2000] or None
    return AiDraft(
        work_date=work_date,
        code=code,
        hours=to_quarters(Decimal(minutes) / 60),
        description=description,
    )


def propose(session: Session, caller: Caller, lines: Any) -> int:
    """Store proposed lines as pending drafts; all lines are checked before any is stored."""
    if not isinstance(lines, list) or not lines:
        raise AilogError("Envía al menos una línea en «lineas».")
    if len(lines) > MAX_PROPOSED:
        raise AilogError(_("Envía como máximo %s líneas por llamada.") % (MAX_PROPOSED,))
    drafts = [_proposed(number, raw) for number, raw in enumerate(lines, start=1)]
    if len(pending_drafts(session, caller.resource_id)) + len(drafts) > MAX_PENDING:
        raise AilogError("Hay demasiados borradores sin revisar; pide a la persona que los revise.")
    for draft in drafts:
        draft.resource_id = caller.resource_id
        draft.token_id = caller.token_id
        session.add(draft)
    session.commit()
    return len(drafts)


def pending_drafts(session: Session, resource_id: int) -> list[AiDraft]:
    return list(
        session.scalars(
            select(AiDraft)
            .where(
                AiDraft.resource_id == resource_id,
                AiDraft.status == DraftStatus.PENDING.value,
            )
            .order_by(AiDraft.work_date, AiDraft.id)
            .limit(MAX_PENDING)
        )
    )


def draft_from_pending(session: Session, resource_id: int) -> list[dict[str, Any]]:
    drafts = pending_drafts(session, resource_id)
    rows = [LogRow(i, d.work_date, d.code, d.hours, d.description) for i, d in enumerate(drafts, 1)]
    lines = draft_from_log(session, resource_id, rows)
    for line, draft in zip(lines, drafts, strict=True):
        line["draft_id"] = draft.id
    return lines


def discard_pending(session: Session, resource_id: int) -> int:
    result = session.execute(
        update(AiDraft)
        .where(AiDraft.resource_id == resource_id, AiDraft.status == DraftStatus.PENDING.value)
        .values(status=DraftStatus.DISCARDED.value)
    )
    session.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
