"""Domain model for a CortexShift Project."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from cortexshift.domain.identifiers import generate_id, utc_now


def generate_project_id() -> str:
    """Generate default ID for projects."""
    return generate_id("proj")


class Project(BaseModel):
    """Represents a software project and repository managed by CortexShift.

    Projects outlive individual tasks and contain long-lived memory such as
    architecture invariants, conventions, and repository location.
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(default_factory=generate_project_id)
    name: str
    repo_path: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def validate_name_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Project name cannot be empty or blank.")
        return stripped
