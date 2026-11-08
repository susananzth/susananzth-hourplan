"""Deviation notes: the lead's or manager's explanation of why a project drifted."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from hourplan.audit import services as audit
from hourplan.audit.models import AuditAction
from hourplan.db import transaction
from hourplan.projects.models import Project
from hourplan.reports.compare import ReportError
from hourplan.reports.models import DeviationNote
from hourplan.timesheet.models import TimeEntry

MAX_LENGTH = 5000


def add_note(
    session: Session, project_id: int, body: str, *, actor_id: int | None
) -> DeviationNote:
    body = body.strip()
    if not body:
        raise ReportError("Escribe la nota de desvío.")
    if len(body) > MAX_LENGTH:
        raise ReportError(f"La nota es demasiado larga (máximo {MAX_LENGTH} caracteres).")
    if session.get(Project, project_id) is None:
        raise ReportError("No se encontró ese proyecto.")
    with transaction(session):
        note = DeviationNote(project_id=project_id, author_id=actor_id, body=body)
        session.add(note)
        session.flush()
        audit.record(
            session,
            action=AuditAction.CREATE,
            entity_type="deviation_note",
            entity_id=note.id,
            actor_id=actor_id,
            changes={"project_id": project_id},
        )
    return note


def project_notes(session: Session, project_id: int) -> list[DeviationNote]:
    return list(
        session.scalars(
            select(DeviationNote)
            .where(DeviationNote.project_id == project_id)
            .order_by(DeviationNote.created_at.desc(), DeviationNote.id.desc())
        )
    )


def notes_for_resource(session: Session, resource_id: int) -> list[tuple[Project, DeviationNote]]:
    """Notes on the projects a person has logged hours on (read-only for them)."""
    worked = select(TimeEntry.project_id).where(
        TimeEntry.resource_id == resource_id, TimeEntry.project_id.is_not(None)
    )
    rows = session.execute(
        select(Project, DeviationNote)
        .join(DeviationNote, DeviationNote.project_id == Project.id)
        .where(Project.id.in_(worked))
        .order_by(DeviationNote.created_at.desc(), DeviationNote.id.desc())
    )
    return [(p, n) for p, n in rows]
