"""Port defining persistence boundaries for CortexShift state."""

from contextlib import AbstractContextManager
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
    def task_write_transaction(self) -> AbstractContextManager[None]:
        """Serialize Task reads and writes until commit, or roll back on error.

        Resolve the active ID and load existing Tasks inside this scope before
        mutating them. Nested write transactions are unsupported.
        """
        ...

    def save_task(self, task: Task) -> None:
        """Persist a new Task or one loaded in the current Task write transaction."""
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
        """Set or clear the active Task pointer inside a Task write transaction."""
        ...

    # Schema inspection
    def get_schema_version(self) -> int:
        """Retrieve the current applied database schema version."""
        ...
