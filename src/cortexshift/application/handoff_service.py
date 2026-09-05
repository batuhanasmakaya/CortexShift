"""Application service for querying persisted canonical handoff history."""

from pathlib import Path

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.locator import ProjectLocator
from cortexshift.domain.errors import HandoffNotFoundError, ProjectNotInitializedError
from cortexshift.domain.handoff import HandoffRecord


class HandoffService:
    """Provides read access to durable handoff records for the current project."""

    def __init__(self, project_locator: type[ProjectLocator] = ProjectLocator) -> None:
        self._locator = project_locator

    def _open(self, start_dir: Path | str | None) -> tuple[str, SQLiteStateStore]:
        """Resolve the initialized project and open its handoff store."""
        start_path = Path(start_dir) if start_dir is not None else None
        project_root = self._locator.find_project_root(start_path)
        if project_root is None:
            raise ProjectNotInitializedError()

        db_path = self._locator.get_database_path(project_root)
        store = SQLiteStateStore(db_path, auto_migrate=False)
        try:
            project = store.get_default_project()
            if project is None:
                raise ProjectNotInitializedError()
            return project.id, store
        except Exception:
            store.close()
            raise

    def list_handoffs(
        self,
        start_dir: Path | str | None = None,
        limit: int = 20,
        task_id: str | None = None,
    ) -> list[HandoffRecord]:
        """List handoffs for the current project, ordered newest first."""
        project_id, store = self._open(start_dir)
        try:
            return store.list_handoffs(project_id=project_id, task_id=task_id, limit=limit)
        finally:
            store.close()

    def get_handoff(
        self,
        handoff_id: str,
        start_dir: Path | str | None = None,
    ) -> HandoffRecord:
        """Retrieve a specific handoff record.

        Raises:
            ProjectNotInitializedError: If no initialized project is found.
            HandoffNotFoundError: If the handoff identifier does not exist.
        """
        _, store = self._open(start_dir)
        try:
            handoff = store.get_handoff(handoff_id)
            if handoff is None:
                raise HandoffNotFoundError(handoff_id)
            return handoff
        finally:
            store.close()
