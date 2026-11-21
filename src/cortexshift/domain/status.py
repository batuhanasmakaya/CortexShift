"""Domain models representing project and task status."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from cortexshift.domain.handoff import HandoffStatus
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import SessionStatus
from cortexshift.domain.task import TaskStatus


class ProgressSummary(BaseModel):
    """Summary counts of task progression items."""

    model_config = ConfigDict(frozen=True)

    completed: int = 0
    remaining: int = 0
    issues: int = 0


class ActiveTaskSummary(BaseModel):
    """Summary of the currently active task on a project."""

    model_config = ConfigDict(frozen=True)

    id: str
    title: str
    status: TaskStatus
    objective: str
    progress: ProgressSummary


class ObjectiveRetrieval(StrEnum):
    NOT_REQUESTED = "not_requested"
    UNCONFIRMED = "unconfirmed"
    PARTIAL = "partial"
    COMPLETE = "complete"


class InvocationSummary(BaseModel):
    id: str
    provider_id: ProviderId
    status: SessionStatus
    resumed_from_session_id: str | None = None
    continued_from_session_id: str | None = None
    handoff_status: HandoffStatus | None = None
    objective_retrieval: ObjectiveRetrieval


class ProjectStatus(BaseModel):
    """Comprehensive status of an initialized CortexShift project."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    name: str
    repo_path: str
    state_file: str
    schema_version: int
    active_task: ActiveTaskSummary | None = None
    latest_session: InvocationSummary | None = None
    workspace_busy: bool | None = None
    unfinalized_sessions: int = 0
