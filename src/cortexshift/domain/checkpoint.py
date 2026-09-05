"""Domain model for task checkpoints."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.domain.identifiers import generate_id, utc_now


def generate_checkpoint_id() -> str:
    """Generate default ID for checkpoints."""
    return generate_id("cp")


class Checkpoint(BaseModel):
    """A point-in-time progress snapshot of an active task.

    Checkpoints capture the core working state so that another agent can recover
    even if the current session terminates abruptly without generating an exit handoff.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=generate_checkpoint_id)
    task_id: str
    session_id: str | None = None
    done: list[str] = Field(default_factory=list)
    current: str | None = None
    next_steps: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    files: list[str] = Field(default_factory=list)
    test_summary: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
