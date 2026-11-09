from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hourplan.db import Base, IdMixin, LongHours, TimestampMixin, enum_check, not_blank


class ProjectKind(StrEnum):
    PROJECT = "project"
    TICKET = "ticket"


class Methodology(StrEnum):
    """How a project is run. It changes which screens are offered, never the stored hours."""

    WATERFALL = "waterfall"
    SCRUM = "scrum"
    KANBAN = "kanban"
    HYBRID = "hybrid"
    STORIES = "stories"


class EpicStatus(StrEnum):
    OPEN = "open"
    DONE = "done"


class SprintStatus(StrEnum):
    PLANNED = "planned"
    ACTIVE = "active"
    CLOSED = "closed"


class PhaseType(StrEnum):
    """System behaviour depends on the type, never on the phase name."""

    PLANNING = "planning"
    CONSTRUCTION = "construction"
    VERIFICATION = "verification"
    APPROVAL = "approval"
    DEPLOYMENT = "deployment"


class PhaseStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    SKIPPED = "skipped"


class CycleOutcome(StrEnum):
    PENDING = "pending"
    RETURNED = "returned"
    APPROVED = "approved"


class LinkType(StrEnum):
    CAUSED_BY = "caused_by"
    RELATES_TO = "relates_to"
    BLOCKS = "blocks"


class PhaseStepMixin:
    """Name, type and flags shared by template steps and project phases."""

    position: Mapped[int] = mapped_column(SmallInteger)
    name: Mapped[str] = mapped_column(String(120))
    phase_type: Mapped[str] = mapped_column(String(20))
    estimated_by_other_role: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    blocks_next: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    counts_as_delivery: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


def _step_checks() -> tuple[CheckConstraint, ...]:
    return (
        enum_check("phase_type", PhaseType),
        not_blank("name"),
        CheckConstraint("position >= 1", name="position"),
    )


class PhaseTemplate(IdMixin, TimestampMixin, Base):
    __tablename__ = "phase_template"
    __table_args__ = (
        UniqueConstraint("code"),
        not_blank("code"),
        not_blank("name"),
        Index(
            "uq_phase_template_default", "is_default", unique=True, postgresql_where="is_default"
        ),
    )

    code: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    steps: Mapped[list[PhaseTemplateStep]] = relationship(
        back_populates="template",
        order_by="PhaseTemplateStep.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PhaseTemplateStep(PhaseStepMixin, IdMixin, Base):
    __tablename__ = "phase_template_step"
    __table_args__ = (
        *_step_checks(),
        UniqueConstraint("template_id", "position", deferrable=True, initially="DEFERRED"),
    )

    template_id: Mapped[int] = mapped_column(ForeignKey("phase_template.id", ondelete="CASCADE"))

    template: Mapped[PhaseTemplate] = relationship(back_populates="steps")


project_repository = Table(
    "project_repository",
    Base.metadata,
    Column("project_id", ForeignKey("project.id", ondelete="CASCADE"), primary_key=True),
    Column("repository_id", ForeignKey("repository.id", ondelete="CASCADE"), primary_key=True),
)


class Project(IdMixin, TimestampMixin, Base):
    """A project or a ticket. Its code comes from an external system and is unique per kind."""

    __tablename__ = "project"
    __table_args__ = (
        enum_check("kind", ProjectKind),
        not_blank("code"),
        not_blank("title"),
        CheckConstraint("parent_id <> id", name="parent_not_self"),
        enum_check("methodology", Methodology),
        # A ticket's epic and sprint belong to the project the ticket hangs from.
        CheckConstraint(
            "(epic_id IS NULL AND sprint_id IS NULL) OR parent_id IS NOT NULL",
            name="agile_needs_parent",
        ),
        ForeignKeyConstraint(
            ["epic_id", "parent_id"],
            ["epic.id", "epic.project_id"],
            name="fk_project_epic",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["sprint_id", "parent_id"],
            ["sprint.id", "sprint.project_id"],
            name="fk_project_sprint",
            use_alter=True,
        ),
        CheckConstraint(
            "(base_effort_hours IS NULL OR base_effort_hours >= 0)"
            " AND (contingency_hours IS NULL OR contingency_hours >= 0)",
            name="hours",
        ),
    )

    kind: Mapped[str] = mapped_column(String(10), default=ProjectKind.PROJECT)
    methodology: Mapped[str] = mapped_column(
        String(10), default=Methodology.WATERFALL, server_default=text("'waterfall'")
    )
    epic_id: Mapped[int | None] = mapped_column(BigInteger)
    sprint_id: Mapped[int | None] = mapped_column(BigInteger)
    code: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("project.id"))
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio.id"))
    requirement_type_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    priority_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    status_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    phase_template_id: Mapped[int | None] = mapped_column(ForeignKey("phase_template.id"))
    manager_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("user_account.id", ondelete="SET NULL"))
    base_effort_hours: Mapped[Decimal | None] = mapped_column(LongHours)
    # Reserve for incidents and tickets during the project, kept apart from the base effort.
    contingency_hours: Mapped[Decimal | None] = mapped_column(LongHours)
    closed_on: Mapped[date | None] = mapped_column(Date)
    # Free-text log of the project (the "bitácora" column of the backlog sheet).
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )

    phases: Mapped[list[ProjectPhase]] = relationship(
        back_populates="project",
        order_by="ProjectPhase.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Project {self.kind}:{self.code}>"


Index("uq_project_kind_code", Project.kind, func.upper(Project.code), unique=True)
Index(None, Project.parent_id)


class Epic(IdMixin, TimestampMixin, Base):
    """A large piece of work inside a project that groups its stories (tickets)."""

    __tablename__ = "epic"
    __table_args__ = (
        UniqueConstraint("id", "project_id"),
        enum_check("status", EpicStatus),
        not_blank("name"),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(10), default=EpicStatus.OPEN, server_default=text("'open'")
    )


Index("uq_epic_project_name", Epic.project_id, func.lower(Epic.name), unique=True)


class Sprint(IdMixin, TimestampMixin, Base):
    """A time box of a project. Only one is active at a time; closed sprints stay as history."""

    __tablename__ = "sprint"
    __table_args__ = (
        UniqueConstraint("id", "project_id"),
        enum_check("status", SprintStatus),
        not_blank("name"),
        CheckConstraint("end_date >= start_date", name="dates"),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    goal: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(
        String(10), default=SprintStatus.PLANNED, server_default=text("'planned'")
    )


Index("uq_sprint_project_name", Sprint.project_id, func.lower(Sprint.name), unique=True)
Index(
    "uq_sprint_one_active",
    Sprint.project_id,
    unique=True,
    postgresql_where=text("status = 'active'"),
)


class ProjectPhase(PhaseStepMixin, IdMixin, Base):
    """A phase of one project, copied from its template step and adjustable per project."""

    __tablename__ = "project_phase"

    __table_args__ = (
        *_step_checks(),
        enum_check("status", PhaseStatus),
        UniqueConstraint("project_id", "position", deferrable=True, initially="DEFERRED"),
        # Target of composite foreign keys that keep a phase inside its project.
        UniqueConstraint("id", "project_id", name="uq_project_phase_id_project"),
        CheckConstraint("planned_end >= planned_start", name="planned_dates"),
        CheckConstraint("actual_end >= actual_start", name="actual_dates"),
        CheckConstraint(
            "(base_effort_hours IS NULL OR base_effort_hours >= 0)"
            " AND (contingency_hours IS NULL OR contingency_hours >= 0)",
            name="hours",
        ),
    )

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    template_step_id: Mapped[int | None] = mapped_column(
        ForeignKey("phase_template_step.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(20), default=PhaseStatus.PENDING, server_default=text("'pending'")
    )
    planned_start: Mapped[date | None] = mapped_column(Date)
    planned_end: Mapped[date | None] = mapped_column(Date)
    # Normally derived from time entries; stored for imported history or a lead's correction.
    actual_start: Mapped[date | None] = mapped_column(Date)
    actual_end: Mapped[date | None] = mapped_column(Date)
    base_effort_hours: Mapped[Decimal | None] = mapped_column(LongHours)
    contingency_hours: Mapped[Decimal | None] = mapped_column(LongHours)

    project: Mapped[Project] = relationship(back_populates="phases")
    cycles: Mapped[list[PhaseCycle]] = relationship(
        back_populates="phase",
        order_by="PhaseCycle.number",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class PhaseCycle(IdMixin, TimestampMixin, Base):
    """One round of a verification or approval phase (V-1, V-2…).

    A returned round sends work back to construction: later construction hours are rework.
    """

    __tablename__ = "phase_cycle"
    __table_args__ = (
        UniqueConstraint("project_phase_id", "number"),
        enum_check("outcome", CycleOutcome),
        CheckConstraint("number >= 1", name="number"),
        CheckConstraint("resolved_on >= submitted_on", name="dates"),
        CheckConstraint("outcome = 'pending' OR resolved_on IS NOT NULL", name="resolved"),
    )

    project_phase_id: Mapped[int] = mapped_column(
        ForeignKey("project_phase.id", ondelete="CASCADE")
    )
    number: Mapped[int] = mapped_column(SmallInteger)
    submitted_on: Mapped[date] = mapped_column(Date)
    resolved_on: Mapped[date | None] = mapped_column(Date)
    outcome: Mapped[str] = mapped_column(
        String(10), default=CycleOutcome.PENDING, server_default=text("'pending'")
    )
    observations: Mapped[str | None] = mapped_column(Text)
    rework_category_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    rework_reason_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    decided_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    # Inferred from historical dates rather than recorded when it happened.
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    phase: Mapped[ProjectPhase] = relationship(back_populates="cycles")


class ChangeRequest(IdMixin, TimestampMixin, Base):
    """Change of scope (not execution rework)."""

    __tablename__ = "change_request"
    __table_args__ = (not_blank("description"), Index(None, "project_id"))

    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    requested_on: Mapped[date] = mapped_column(Date)
    size_id: Mapped[int | None] = mapped_column(ForeignKey("catalog_item.id"))
    hours_impact: Mapped[Decimal | None] = mapped_column(LongHours)
    description: Mapped[str] = mapped_column(Text)
    approved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("user_account.id", ondelete="SET NULL")
    )
    approved_on: Mapped[date | None] = mapped_column(Date)


class WorkItemLink(IdMixin, TimestampMixin, Base):
    """``source`` *link_type* ``target``: e.g. a rework ticket is caused_by its origin project."""

    __tablename__ = "work_item_link"
    __table_args__ = (
        UniqueConstraint("source_id", "target_id", "link_type"),
        enum_check("link_type", LinkType),
        CheckConstraint("source_id <> target_id", name="not_self"),
        Index(None, "target_id"),
    )

    source_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    target_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    link_type: Mapped[str] = mapped_column(String(20))
