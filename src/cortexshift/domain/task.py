"""Domain model for a CortexShift Task."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cortexshift.domain.identifiers import generate_id, utc_now


class TaskStatus(StrEnum):
    """Lifecycle status of a persistent development task."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


def generate_task_id() -> str:
    """Generate default ID for tasks."""
    return generate_id("task")


class Task(BaseModel):
    """Represents a persistent development task.

    The task is the central abstraction of CortexShift. Coding agents are temporary
    workers operating sequentially on this task.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=generate_task_id)
    project_id: str
    title: str
    objective: str
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    completed_items: list[str] = Field(default_factory=list)
    current_work: str | None = None
    remaining_items: list[str] = Field(default_factory=list)
    known_issues: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("title", "objective")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Field cannot be empty or blank.")
        return stripped
