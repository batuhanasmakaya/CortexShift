"""Domain model for a coding agent execution session."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.domain.provider import ProviderId


class SessionStatus(StrEnum):
    """Execution status of an individual agent session."""

    INITIALIZING = "initializing"
    ACTIVE = "active"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class SessionExitReason(StrEnum):
    """Reason why an agent session concluded or halted."""

    NORMAL_COMPLETION = "normal_completion"
    USER_INTERRUPTED = "user_interrupted"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
    PROCESS_CRASHED = "process_crashed"
    UNEXPECTED_TERMINATION = "unexpected_termination"
    UNKNOWN = "unknown"


def generate_session_id() -> str:
    """Generate default ID for sessions."""
    return generate_id("sess")


class Session(BaseModel):
    """An execution session by a specific coding agent working on a task.

    Sessions are transient worker executions; tasks outlast sessions.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=generate_session_id)
    task_id: str
    provider_id: ProviderId
    native_session_id: str | None = None
    status: SessionStatus = SessionStatus.INITIALIZING
    started_at: datetime = Field(default_factory=utc_now)
    ended_at: datetime | None = None
    exit_reason: SessionExitReason | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
