"""What each methodology offers. Data, so a new profile is one more row."""

from dataclasses import dataclass

from hourplan.projects.models import Methodology
from hourplan.shared.translate import N_


@dataclass(frozen=True)
class Profile:
    label: str
    summary: str
    phases: bool  # phase table, rounds and Gantt
    sprints: bool
    epics: bool
    board: bool  # board by status


PROFILES: dict[str, Profile] = {
    Methodology.WATERFALL: Profile(
        N_("Cascada"), N_("Fases en secuencia con rondas de aprobación."), True, False, False, False
    ),
    Methodology.SCRUM: Profile(
        N_("Scrum"), N_("Sprints, épicas e historias con tablero."), False, True, True, True
    ),
    Methodology.KANBAN: Profile(
        N_("Kanban"), N_("Flujo continuo en un tablero por estado."), False, False, False, True
    ),
    Methodology.HYBRID: Profile(
        N_("Híbrido"),
        N_("Fases de cascada con sprints dentro de la construcción."),
        True,
        True,
        False,
        True,
    ),
    Methodology.STORIES: Profile(
        N_("Historias"), N_("Épicas e historias sin sprints fijos."), False, False, True, True
    ),
}


def profile_of(methodology: str) -> Profile:
    return PROFILES.get(methodology, PROFILES[Methodology.WATERFALL])


def has_agile(methodology: str) -> bool:
    profile = profile_of(methodology)
    return profile.sprints or profile.epics or profile.board
