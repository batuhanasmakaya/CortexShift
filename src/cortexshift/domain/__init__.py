"""Core domain models for CortexShift."""

from cortexshift.domain.checkpoint import Checkpoint
from cortexshift.domain.doctor import (
    AuthenticationStatus,
    DoctorReport,
    PlatformInfo,
    ProviderDiagnostic,
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
from cortexshift.domain.task import Task, TaskStatus

__all__ = [
    "AuthenticationStatus",
    "Checkpoint",
    "DoctorReport",
    "GitSnapshot",
    "Handoff",
    "PlatformInfo",
    "Project",
    "ProviderCapabilities",
    "ProviderDiagnostic",
    "ProviderId",
    "Session",
    "SessionExitReason",
    "SessionStatus",
    "Task",
    "TaskStatus",
    "PROVIDER_ANTIGRAVITY",
    "PROVIDER_CLAUDE",
    "PROVIDER_CODEX",
    "generate_id",
    "utc_now",
]
