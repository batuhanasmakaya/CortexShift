"""Core domain models for CortexShift."""

from cortexshift.domain.checkpoint import Checkpoint
from cortexshift.domain.doctor import (
    AuthenticationStatus,
    DoctorReport,
    PlatformInfo,
    ProviderDiagnostic,
)
from cortexshift.domain.errors import (
    CortexShiftError,
    DatabaseStateError,
    NoActiveTaskError,
    ProjectAlreadyInitializedError,
    ProjectConflictError,
    ProjectNotInitializedError,
    StateCorruptionError,
    TaskAlreadyCompletedError,
    TaskNotActivatableError,
    TaskNotFoundError,
    UnsupportedSchemaVersionError,
)
from cortexshift.domain.git import GitSnapshot
from cortexshift.domain.handoff import Handoff
from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.domain.project import Project
from cortexshift.domain.provider import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    ProviderCapabilities,
    ProviderId,
)
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.status import ActiveTaskSummary, ProgressSummary, ProjectStatus
from cortexshift.domain.task import Task, TaskStatus

__all__ = [
    "ActiveTaskSummary",
    "AuthenticationStatus",
    "Checkpoint",
    "CortexShiftError",
    "DatabaseStateError",
    "DoctorReport",
    "GitSnapshot",
    "Handoff",
    "NoActiveTaskError",
    "PlatformInfo",
    "ProgressSummary",
    "Project",
    "ProjectAlreadyInitializedError",
    "ProjectConflictError",
    "ProjectNotInitializedError",
    "ProjectStatus",
    "ProviderCapabilities",
    "ProviderDiagnostic",
    "ProviderId",
    "Session",
    "SessionExitReason",
    "SessionStatus",
    "StateCorruptionError",
    "Task",
    "TaskAlreadyCompletedError",
    "TaskNotActivatableError",
    "TaskNotFoundError",
    "TaskStatus",
    "UnsupportedSchemaVersionError",
    "PROVIDER_ANTIGRAVITY",
    "PROVIDER_CLAUDE",
    "PROVIDER_CODEX",
    "generate_id",
    "utc_now",
]
