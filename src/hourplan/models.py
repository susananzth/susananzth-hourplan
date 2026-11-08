"""Imports every model so Alembic sees the full metadata."""

from hourplan.audit.models import AuditLog
from hourplan.auth.models import User, UserRole
from hourplan.catalogs.models import Application, CatalogItem, Portfolio, Repository
from hourplan.db import Base
from hourplan.imports.models import ImportBatch, ImportIssue
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
from hourplan.reports.models import ComplianceSnapshot, DeviationNote
from hourplan.resources.models import Resource
from hourplan.timer.models import AlertFired, AlertSetting, TimerPreference, TimerSession
from hourplan.timesheet.models import Absence, TimeBankLedger, TimeEntry
from hourplan.voice.models import VoiceDictation

metadata = Base.metadata

__all__ = [
    "Absence",
    "AlertFired",
    "AlertSetting",
    "Application",
    "AuditLog",
    "CatalogItem",
    "ChangeRequest",
    "ComplianceSnapshot",
    "DeviationNote",
    "EvaluationPolicy",
    "Holiday",
    "ImportBatch",
    "ImportIssue",
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
    "TimerPreference",
    "TimerSession",
    "User",
    "UserRole",
    "VoiceDictation",
    "WorkItemLink",
    "metadata",
]
