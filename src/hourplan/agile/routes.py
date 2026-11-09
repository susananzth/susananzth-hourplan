"""Agile screens of a project: board with backlog, sprints and epics (offered per methodology)."""

from collections.abc import Callable
from datetime import date, datetime
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
from hourplan.projects.models import EpicStatus, Project, SprintStatus
from hourplan.shared.i18n import _
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


def _points(raw: str | None) -> int | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError(_("Puntos: escribe un número entero, por ejemplo 3 u 8."))
    return int(raw)


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
        if isinstance(err, ValidationError):
            message = validation_message(err)
        elif isinstance(err, services.AgileError):
            message = err.translated(_)
        else:
            message = _(str(err))
        flash(message, "error")
    else:
        flash(_(success), "info")
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
    columns.insert(0, (None, _("Sin estado"), [x for x in stories if x.status_id is None]))
    return _page(
        project,
        "board",
        "agile/board.html",
        columns=columns,
        limits=services.wip_limits(db.session, project_id),
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
                story_points=_points(form.get("puntos")),
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
            story_points=_points(request.form.get("puntos")),
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


@bp.post("/limites")
@roles_required(*EDITORS)
def set_limits(project_id: int) -> Response:
    _project(project_id, "board")
    statuses = catalogs.list_items(db.session, CatalogKind.PROJECT_STATUS)

    def run() -> None:
        limits: dict[int, int | None] = {}
        for status in statuses:
            raw = (request.form.get(f"limite-{status.id}") or "").strip()
            if raw and not raw.isdigit():
                raise ValueError(_("Límite: escribe un número entero o déjalo vacío."))
            limits[status.id] = int(raw) if raw else None
        services.set_wip_limits(db.session, project_id, limits, actor_id=_actor())

    return _act(project_id, "Límites guardados.", "agile.board", run)


@bp.get("/sprints")
@roles_required(*VIEWERS)
def sprints(project_id: int) -> str:
    project = _project(project_id, "sprints")
    sprints = services.list_sprints(db.session, project_id)
    chosen = _pick_sprint(sprints, _int(request.args.get("sprint")))
    return _page(
        project,
        "sprints",
        "agile/sprints.html",
        sprints=sprints,
        totals=services.sprint_totals(db.session, project_id),
        status_labels=SPRINT_STATUS,
        chosen=chosen,
        chart=(
            None
            if chosen is None
            else chart(
                services.burndown(
                    db.session, project_id, chosen.id, today=datetime.now().astimezone().date()
                )
            )
        ),
        planned=[s for s in sprints if s.status == SprintStatus.PLANNED.value],
    )


def _pick_sprint(sprints: list[Any], wanted: int | None) -> Any:
    """The sprint asked for, else the active one, else the last closed one."""
    by_id = {s.id: s for s in sprints}
    if wanted in by_id:
        return by_id[wanted]
    active = [s for s in sprints if s.status == SprintStatus.ACTIVE.value]
    closed = [s for s in sprints if s.status == SprintStatus.CLOSED.value]
    return (active or closed[-1:] or [None])[0]


def chart(data: services.Burndown) -> dict[str, Any]:
    """Points of an SVG line chart for the burndown, in a fixed 600x240 box."""
    width, height, pad = 600, 240, 32
    top = float(data.scope) or 1.0
    steps = max(len(data.days) - 1, 1)

    def xy(n: int, value: Decimal) -> str:
        x = pad + (width - 2 * pad) * n / steps
        y = height - pad - (height - 2 * pad) * float(value) / top
        return f"{x:.1f},{y:.1f}"

    return {
        "width": width,
        "height": height,
        "pad": pad,
        "ideal": " ".join(xy(n, v) for n, v in enumerate(data.ideal)),
        "real": " ".join(xy(n, v) for n, v in enumerate(data.remaining)),
        "data": data,
        "rows": [
            (day, data.ideal[n], data.remaining[n] if n < len(data.remaining) else None)
            for n, day in enumerate(data.days)
        ],
    }


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
        lambda: services.close_sprint(
            db.session,
            project_id,
            sprint_id,
            carry_to=_int(request.form.get("destino")),
            actor_id=_actor(),
        ),
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
