"""Port defining persistence boundaries for CortexShift state."""

from typing import Protocol, runtime_checkable

from cortexshift.domain.project import Project
from cortexshift.domain.task import Task


@runtime_checkable
class StateStore(Protocol):
    """Abstract port for persisting and querying CortexShift entities.

    Isolates domain and application logic from the underlying storage mechanism
    (e.g., local SQLite, in-memory test store).
    """

    # Project operations
    def save_project(self, project: Project) -> None:
        """Persist or update a Project."""
        ...

    def get_project(self, project_id: str) -> Project | None:
        """Retrieve a Project by its stable identifier."""
        ...

    def get_default_project(self) -> Project | None:
        """Retrieve the canonical project associated with this project-local store."""
        ...

    # Task operations
    def save_task(self, task: Task) -> None:
        """Persist or update a Task."""
        ...

    def get_task(self, task_id: str) -> Task | None:
        """Retrieve a Task by its stable identifier."""
        ...

    def list_tasks(self, project_id: str) -> list[Task]:
        """List all tasks associated with a given project."""
        ...

    # Runtime / Active Task state
    def get_active_task_id(self, project_id: str) -> str | None:
        """Retrieve the identifier of the active task for the project, if any."""
        ...

    def set_active_task_id(self, project_id: str, task_id: str | None) -> None:
        """Set or clear the active task identifier for the project."""
        ...

    # Schema inspection
    def get_schema_version(self) -> int:
        """Retrieve the current applied database schema version."""
        ...
