"""Domain models representing project and task status."""

from pydantic import BaseModel, ConfigDict

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


class ProjectStatus(BaseModel):
    """Comprehensive status of an initialized CortexShift project."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    name: str
    repo_path: str
    state_file: str
    schema_version: int
    active_task: ActiveTaskSummary | None = None
