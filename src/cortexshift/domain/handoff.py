"""Domain model for canonical cross-agent task handoffs."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.domain.git import GitSnapshot
from cortexshift.domain.identifiers import generate_id, utc_now


def generate_handoff_id() -> str:
    """Generate default ID for handoffs."""
    return generate_id("handoff")


class Handoff(BaseModel):
    """Canonical, provider-independent task handoff payload.

    Contains the structured context transferred to an incoming agent,
    instructing it on objectives, decisions, progress, and reality verification.
    """

    model_config = ConfigDict(frozen=True)

    # Identifiers & Metadata
    id: str = Field(default_factory=generate_handoff_id)
    task_id: str
    source_session_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    recovery_mode: bool = False

    # Canonical Protocol Sections (14 Fields)
    project_name: str
    original_objective: str
    requirements: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    completed: list[str] = Field(default_factory=list)
    current_work: str | None = None
    remaining: list[str] = Field(default_factory=list)
    important_decisions: list[str] = Field(default_factory=list)
    files_touched: list[str] = Field(default_factory=list)
    test_status: str
    known_issues: list[str] = Field(default_factory=list)
    git_state: GitSnapshot | None = None
    do_not_redo: list[str] = Field(default_factory=list)
    recommended_next_action: str

    metadata: dict[str, Any] = Field(default_factory=dict)
