"""Port defining persistence boundaries for CortexShift state."""

from typing import Protocol, runtime_checkable

from cortexshift.domain.checkpoint import Checkpoint
from cortexshift.domain.handoff import Handoff
from cortexshift.domain.project import Project
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task


@runtime_checkable
class StateStore(Protocol):
    """Abstract port for persisting and querying CortexShift entities.

    Isolates domain and application logic from the underlying storage mechanism
    (e.g., local SQLite, in-memory test store, filesystem JSON).
    """

    # Project operations
    def save_project(self, project: Project) -> None:
        """Persist or update a Project."""
        ...

    def get_project(self, project_id: str) -> Project | None:
        """Retrieve a Project by its stable identifier."""
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

    # Session operations
    def save_session(self, session: Session) -> None:
        """Persist or update an agent Session."""
        ...

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a Session by its stable identifier."""
        ...

    def list_sessions(self, task_id: str) -> list[Session]:
        """List all sessions executed against a given task."""
        ...

    # Checkpoint operations
    def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Persist a task Checkpoint."""
        ...

    def get_latest_checkpoint(self, task_id: str) -> Checkpoint | None:
        """Retrieve the most recent checkpoint for a given task."""
        ...

    # Handoff operations
    def save_handoff(self, handoff: Handoff) -> None:
        """Persist a task Handoff."""
        ...

    def get_latest_handoff(self, task_id: str) -> Handoff | None:
        """Retrieve the most recent handoff for a given task."""
        ...
