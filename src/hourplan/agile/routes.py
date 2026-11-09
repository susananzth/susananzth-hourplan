"""Agile screens of a project: board with backlog, sprints and epics (offered per methodology)."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from pydantic import ValidationError
from werkzeug.wrappers import Response

from hourplan.agile import services
from hourplan.auth.permissions import roles_required
from hourplan.auth.roles import Role
from hourplan.catalogs import services as catalogs
from hourplan.catalogs.models import CatalogKind
from hourplan.extensions import db
from hourplan.projects.methodology import profile_of
from hourplan.projects.models import EpicStatus, Project
from hourplan.shared.labels import label, validation_message

bp = Blueprint(
    "agile", __name__, url_prefix="/proyectos/<int:project_id>/agil", template_folder="templates"
)

VIEWERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER, Role.OBSERVER)
EDITORS = (Role.ADMIN, Role.PM, Role.LEAD)
# Developers move their own work along the board but do not plan it.
MOVERS = (*EDITORS, Role.DEVELOPER)
SPRINT_STATUS = {"planned": "Planificado", "active": "Activo", "closed": "Cerrado"}


def _int(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def _hours(raw: str | None) -> Decimal | None:
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise ValueError(
            f"{label('base_effort_hours')}: escribe un número, por ejemplo 8 o 2,5."
        ) from None


def _day(raw: str | None, field: str) -> date:
    try:
        return date.fromisoformat((raw or "").strip())
    except ValueError:
        raise ValueError(f"{label(field)}: la fecha no es válida.") from None


def _project(project_id: int, feature: str | None = None) -> Project:
    project = db.session.get(Project, project_id)
    if project is None or project.kind != "project":
        abort(404)
    if feature and not getattr(profile_of(project.methodology), feature):
        abort(404)
    return project


def _act(project_id: int, success: str, target: str, action: Callable[[], Any]) -> Response:
    """Run a change, show its message and go back to the page it came from."""
    try:
        action()
    except (ValueError, ValidationError) as err:
        db.session.rollback()
        flash(validation_message(err) if isinstance(err, ValidationError) else str(err), "error")
    else:
        flash(success, "info")
    return redirect(url_for(target, project_id=project_id))


def _actor() -> int:
    return int(current_user.id)


def _page(project: Project, feature: str, template: str, **context: Any) -> str:
    return render_template(
        template,
        project=project,
        profile=profile_of(project.methodology),
        can_edit=current_user.has_role(*EDITORS),
        can_move=current_user.has_role(*MOVERS),
        **context,
    )


@bp.get("/tablero")
@roles_required(*VIEWERS)
def board(project_id: int) -> str:
    project = _project(project_id, "board")
    statuses = catalogs.list_items(db.session, CatalogKind.PROJECT_STATUS)
    stories = services.list_stories(db.session, project_id)
    columns: list[tuple[int | None, str, list[Project]]] = [
        (s.id, s.name, [x for x in stories if x.status_id == s.id]) for s in statuses
    ]
    columns.insert(0, (None, "Sin estado", [x for x in stories if x.status_id is None]))
    return _page(
        project,
        "board",
        "agile/board.html",
        columns=columns,
        stories=stories,
        epics={e.id: e for e in services.list_epics(db.session, project_id)},
        sprints={s.id: s for s in services.list_sprints(db.session, project_id)},
    )


@bp.post("/historias")
@roles_required(*EDITORS)
def create_story(project_id: int) -> Response:
    _project(project_id)
    form = request.form

    def run() -> None:
        services.create_story(
            db.session,
            project_id,
            services.StoryData(
                code=form.get("codigo", ""),
                title=form.get("titulo", ""),
                epic_id=_int(form.get("epica")),
                sprint_id=_int(form.get("sprint")),
                base_effort_hours=_hours(form.get("esfuerzo")),
            ),
            actor_id=_actor(),
        )

    return _act(project_id, "Historia creada.", "agile.board", run)


@bp.post("/historias/<int:story_id>/asignar")
@roles_required(*EDITORS)
def assign_story(project_id: int, story_id: int) -> Response:
    _project(project_id)

    def run() -> None:
        services.assign_story(
            db.session,
            project_id,
            story_id,
            epic_id=_int(request.form.get("epica")),
            sprint_id=_int(request.form.get("sprint")),
            actor_id=_actor(),
        )

    return _act(project_id, "Historia actualizada.", "agile.board", run)


@bp.post("/historias/<int:story_id>/estado")
@roles_required(*MOVERS)
def move_story(project_id: int, story_id: int) -> Response:
    _project(project_id, "board")

    def run() -> None:
        services.move_story(
            db.session,
            project_id,
            story_id,
            _int(request.form.get("estado")),
            actor_id=_actor(),
        )

    return _act(project_id, "Historia movida.", "agile.board", run)


@bp.get("/sprints")
@roles_required(*VIEWERS)
def sprints(project_id: int) -> str:
    project = _project(project_id, "sprints")
    return _page(
        project,
        "sprints",
        "agile/sprints.html",
        sprints=services.list_sprints(db.session, project_id),
        totals=services.sprint_totals(db.session, project_id),
        status_labels=SPRINT_STATUS,
    )


@bp.post("/sprints")
@roles_required(*EDITORS)
def create_sprint(project_id: int) -> Response:
    _project(project_id, "sprints")
    form = request.form

    def run() -> None:
        services.create_sprint(
            db.session,
            project_id,
            services.SprintData(
                name=form.get("nombre", ""),
                goal=form.get("objetivo") or None,
                start_date=_day(form.get("inicio"), "start_date"),
                end_date=_day(form.get("fin"), "end_date"),
            ),
            actor_id=_actor(),
        )

    return _act(project_id, "Sprint creado.", "agile.sprints", run)


@bp.post("/sprints/<int:sprint_id>/iniciar")
@roles_required(*EDITORS)
def start_sprint(project_id: int, sprint_id: int) -> Response:
    _project(project_id, "sprints")
    return _act(
        project_id,
        "Sprint iniciado.",
        "agile.sprints",
        lambda: services.start_sprint(db.session, project_id, sprint_id, actor_id=_actor()),
    )


@bp.post("/sprints/<int:sprint_id>/cerrar")
@roles_required(*EDITORS)
def close_sprint(project_id: int, sprint_id: int) -> Response:
    _project(project_id, "sprints")
    return _act(
        project_id,
        "Sprint cerrado.",
        "agile.sprints",
        lambda: services.close_sprint(db.session, project_id, sprint_id, actor_id=_actor()),
    )


@bp.get("/epicas")
@roles_required(*VIEWERS)
def epics(project_id: int) -> str:
    project = _project(project_id, "epics")
    return _page(
        project,
        "epics",
        "agile/epics.html",
        epics=services.list_epics(db.session, project_id),
        totals=services.epic_totals(db.session, project_id),
    )


@bp.post("/epicas")
@roles_required(*EDITORS)
def create_epic(project_id: int) -> Response:
    _project(project_id, "epics")
    form = request.form
    return _act(
        project_id,
        "Épica creada.",
        "agile.epics",
        lambda: services.create_epic(
            db.session,
            project_id,
            services.EpicData(name=form.get("nombre", ""), description=form.get("descripcion")),
            actor_id=_actor(),
        ),
    )


@bp.post("/epicas/<int:epic_id>/estado")
@roles_required(*EDITORS)
def set_epic_status(project_id: int, epic_id: int) -> Response:
    _project(project_id, "epics")
    try:
        status = EpicStatus(request.form.get("estado", ""))
    except ValueError:
        abort(400)
    return _act(
        project_id,
        "Épica actualizada.",
        "agile.epics",
        lambda: services.set_epic_status(
            db.session, project_id, epic_id, status, actor_id=_actor()
        ),
    )
