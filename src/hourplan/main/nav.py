"""Side menu: links grouped by area, filtered by the user's roles (the views check again)."""

from dataclasses import dataclass, field
from typing import Any

from flask import current_app, request, url_for
from flask_login import current_user

from hourplan.auth.roles import Role
from hourplan.shared.i18n import N_, _

WORKERS = (Role.ADMIN, Role.PM, Role.LEAD, Role.DEVELOPER)
MANAGERS = (Role.ADMIN, Role.PM, Role.LEAD)
CATALOG_ROLES = (Role.ADMIN, Role.PM)


@dataclass
class Link:
    label: str
    href: str
    icon: str
    prefix: str
    active: bool = False


@dataclass
class Group:
    label: str
    icon: str
    links: list[Link] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return any(link.active for link in self.links)


def _link(label: str, endpoint: str, icon: str, prefix: str | None = None) -> Link:
    href = url_for(endpoint)
    prefix = prefix or href.rstrip("/")
    path = request.path
    active = path == prefix or path.startswith(prefix + "/")
    return Link(_(label), href, icon, prefix, active)


def _has(*roles: Role) -> bool:
    return bool(current_user.has_role(*roles))


def build() -> list[Group]:
    """Groups with at least one link for the current user."""
    work = Group(_("Mi trabajo"), icon="briefcase")
    work.links.append(
        _link(N_("Mis horas"), "timesheet.week", icon="calendar-clock", prefix="/horas")
    )
    if _has(*WORKERS):
        work.links.append(
            _link(N_("Cronómetro"), "timer.index", icon="timer", prefix="/cronometro")
        )
        if current_app.config.get("VOICE_STT_URL"):
            work.links.append(_link(N_("Voz"), "voice.index", icon="mic", prefix="/voz"))
        work.links.append(
            _link(N_("Bitácora"), "ailog.index", icon="notebook-pen", prefix="/bitacora")
        )
    work.links.append(
        _link(N_("Ausencias"), "absences.index", icon="calendar-x", prefix="/ausencias")
    )
    work.links.append(_link(N_("Banco de horas"), "bank.index", icon="piggy-bank", prefix="/banco"))

    projects = Group(_("Proyectos"), icon="folder-kanban")
    projects.links.append(
        _link(N_("Proyectos"), "projects.index", icon="folder-open", prefix="/proyectos")
    )
    projects.links.append(
        _link(
            N_("Planificación"),
            "planning.gantt_projects",
            icon="chart-gantt",
            prefix="/planificacion",
        )
    )
    if _has(*MANAGERS):
        projects.links.append(
            _link(N_("Informes"), "reports.index", icon="chart-column", prefix="/informes")
        )
    elif _has(Role.DEVELOPER):
        projects.links.append(
            _link(N_("Mi informe"), "reports.mine", icon="chart-column", prefix="/informes")
        )

    catalogs = Group(_("Catálogos"), icon="library")
    if _has(*CATALOG_ROLES):
        catalogs.links += [
            _link(N_("Catálogos"), "admin.catalog_list", icon="list-tree"),
            _link(N_("Carteras"), "admin.portfolio_list", icon="layers"),
            _link(N_("Aplicaciones"), "admin.application_list", icon="app-window"),
            _link(N_("Repositorios"), "admin.repository_list", icon="git-branch"),
        ]

    admin = Group(_("Administración"), icon="settings")
    if _has(Role.ADMIN):
        admin.links += [
            _link(N_("Usuarios"), "admin.user_list", icon="users"),
            _link(N_("Políticas"), "admin.policy_list", icon="scale"),
            _link(N_("Feriados"), "admin.holiday_list", icon="calendar-days"),
        ]
    return [group for group in (work, projects, catalogs, admin) if group.links]


def navigation() -> dict[str, Any]:
    return {"nav_groups": build() if current_user.is_authenticated else []}
