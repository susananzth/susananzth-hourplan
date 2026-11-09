"""What each methodology offers. Data, so a new profile is one more row."""

from dataclasses import dataclass

from hourplan.projects.models import Methodology


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
        "Cascada", "Fases en secuencia con rondas de aprobación.", True, False, False, False
    ),
    Methodology.SCRUM: Profile(
        "Scrum", "Sprints, épicas e historias con tablero.", False, True, True, True
    ),
    Methodology.KANBAN: Profile(
        "Kanban", "Flujo continuo en un tablero por estado.", False, False, False, True
    ),
    Methodology.HYBRID: Profile(
        "Híbrido",
        "Fases de cascada con sprints dentro de la construcción.",
        True,
        True,
        False,
        True,
    ),
    Methodology.STORIES: Profile(
        "Historias", "Épicas e historias sin sprints fijos.", False, False, True, True
    ),
}


def profile_of(methodology: str) -> Profile:
    return PROFILES.get(methodology, PROFILES[Methodology.WATERFALL])


def has_agile(methodology: str) -> bool:
    profile = profile_of(methodology)
    return profile.sprints or profile.epics or profile.board
