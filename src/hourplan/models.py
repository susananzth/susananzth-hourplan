"""Imports every model so Alembic sees the full metadata."""

from hourplan.audit.models import AuditLog
from hourplan.auth.models import User, UserRole
from hourplan.catalogs.models import Application, CatalogItem, Portfolio, Repository
from hourplan.db import Base
from hourplan.planning.models import PlanAllocation, PlanVersion
from hourplan.policies.models import EvaluationPolicy, Holiday
from hourplan.projects.models import (
    ChangeRequest,
    PhaseCycle,
    PhaseTemplate,
    PhaseTemplateStep,
    Project,
    ProjectPhase,
    WorkItemLink,
)
from hourplan.resources.models import Resource
from hourplan.timesheet.models import Absence, TimeBankLedger, TimeEntry

metadata = Base.metadata

__all__ = [
    "Absence",
    "Application",
    "AuditLog",
    "CatalogItem",
    "ChangeRequest",
    "EvaluationPolicy",
    "Holiday",
    "PhaseCycle",
    "PhaseTemplate",
    "PhaseTemplateStep",
    "PlanAllocation",
    "PlanVersion",
    "Portfolio",
    "Project",
    "ProjectPhase",
    "Repository",
    "Resource",
    "TimeBankLedger",
    "TimeEntry",
    "User",
    "UserRole",
    "WorkItemLink",
    "metadata",
]
