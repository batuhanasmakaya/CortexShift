"""Domain model for a coding agent execution session."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.domain.provider import ProviderId


class SessionStatus(StrEnum):
    """Execution status of an individual agent session.

    Phase 4 Active Statuses:
      - INITIALIZING: Session record created before process launch.
      - RUNNING: Session process is actively executing.
      - COMPLETED: Process exited normally with exit code 0.
      - INTERRUPTED: Session was interrupted by user (SIGINT/Ctrl+C, exit code 130).
      - FAILED: Session process exited with non-zero exit code or spawn failed.

    Reserved Future Statuses (not emitted in Phase 4):
      - ACTIVE: Reserved alias for active execution states.
      - TIMED_OUT: Reserved for execution timeout policies.
    """

    INITIALIZING = "initializing"
    RUNNING = "running"
    ACTIVE = "active"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class SessionExitReason(StrEnum):
    """Reason why an agent session concluded or halted.

    Phase 4 Active Exit Reasons:
      - NORMAL_COMPLETION: Native CLI process exited with code 0.
      - USER_INTERRUPTED: User interrupted interactive session via SIGINT (Ctrl+C).
      - PROCESS_CRASHED: Native CLI process exited with non-zero exit code.
      - SPAWN_FAILED: Process runner failed to spawn the native CLI executable.

    Reserved Future Exit Reasons (not emitted in Phase 4):
      - QUOTA_EXHAUSTED: Reserved for future structured API quota exhaustion detection.
      - RATE_LIMITED: Reserved for future provider rate limit detection.
      - UNEXPECTED_TERMINATION: Reserved for unexpected external process termination.
      - UNKNOWN: Reserved fallback for unclassifiable exits.

    Note: Phase 4 does not infer quota exhaustion or rate limits from generic
    non-zero exit codes. Generic non-zero process exits map strictly to PROCESS_CRASHED.
    """

    NORMAL_COMPLETION = "normal_completion"
    USER_INTERRUPTED = "user_interrupted"
    PROCESS_CRASHED = "process_crashed"
    SPAWN_FAILED = "spawn_failed"

    # Reserved for future phases (not emitted in Phase 4):
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMITED = "rate_limited"
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
    exit_code: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
