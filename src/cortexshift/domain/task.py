"""Domain model for a CortexShift Task."""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator, model_validator

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

    @model_validator(mode="before")
    @classmethod
    def _normalize_completed_and_remaining(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Support both completed and completed_items
            if "completed" in data and "completed_items" not in data:
                data["completed_items"] = data.pop("completed")
            # Support both remaining and remaining_items
            if "remaining" in data and "remaining_items" not in data:
                data["remaining_items"] = data.pop("remaining")
        return data

    @field_validator("title", "objective")
    @classmethod
    def validate_non_empty_strings(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Field cannot be empty or blank.")
        return stripped

    @computed_field  # type: ignore[prop-decorator]
    @property
    def completed(self) -> list[str]:
        """Canonical alias for completed_items."""
        return self.completed_items

    @computed_field  # type: ignore[prop-decorator]
    @property
    def remaining(self) -> list[str]:
        """Canonical alias for remaining_items."""
        return self.remaining_items

    @property
    def is_terminal(self) -> bool:
        """Return True if the task is in a terminal state (cannot be resumed/activated)."""
        return self.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)

    def mark_completed(self) -> "Task":
        """Return a copy of this task marked as completed with updated timestamp."""
        now = utc_now()
        return self.model_copy(
            update={
                "status": TaskStatus.COMPLETED,
                "updated_at": now,
            }
        )

    def with_current_work(self, work: str | None) -> "Task":
        """Return a copy of this task with updated current work and timestamp."""
        now = utc_now()
        cleaned = work.strip() if work is not None else None
        return self.model_copy(
            update={
                "current_work": cleaned if cleaned else None,
                "updated_at": now,
            }
        )

    def add_completed_items(self, items: list[str]) -> "Task":
        """Return a copy of this task with additional completed items (deduplicated)."""
        new_items = list(self.completed_items)
        existing_set = set(new_items)
        for item in items:
            cleaned = item.strip()
            if cleaned and cleaned not in existing_set:
                new_items.append(cleaned)
                existing_set.add(cleaned)
        return self.model_copy(
            update={
                "completed_items": new_items,
                "updated_at": utc_now(),
            }
        )

    def add_remaining_items(self, items: list[str]) -> "Task":
        """Return a copy of this task with additional remaining items (deduplicated)."""
        new_items = list(self.remaining_items)
        existing_set = set(new_items)
        for item in items:
            cleaned = item.strip()
            if cleaned and cleaned not in existing_set:
                new_items.append(cleaned)
                existing_set.add(cleaned)
        return self.model_copy(
            update={
                "remaining_items": new_items,
                "updated_at": utc_now(),
            }
        )

    def add_known_issues(self, items: list[str]) -> "Task":
        """Return a copy of this task with additional known issues (deduplicated)."""
        new_items = list(self.known_issues)
        existing_set = set(new_items)
        for item in items:
            cleaned = item.strip()
            if cleaned and cleaned not in existing_set:
                new_items.append(cleaned)
                existing_set.add(cleaned)
        return self.model_copy(
            update={
                "known_issues": new_items,
                "updated_at": utc_now(),
            }
        )

    def add_completed(self, items: list[str]) -> "Task":
        """Alias for add_completed_items."""
        return self.add_completed_items(items)

    def add_remaining(self, items: list[str]) -> "Task":
        """Alias for add_remaining_items."""
        return self.add_remaining_items(items)
