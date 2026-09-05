"""Application service for querying CortexShift session history."""

from pathlib import Path

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.locator import ProjectLocator
from cortexshift.domain.errors import ProjectNotInitializedError, SessionNotFoundError
from cortexshift.domain.session import Session


class SessionService:
    """Provides querying capabilities for agent execution sessions."""

    def __init__(self, project_locator: type[ProjectLocator] = ProjectLocator) -> None:
        self._locator = project_locator

    def list_sessions(
        self,
        start_dir: Path | str | None = None,
        limit: int = 20,
    ) -> list[Session]:
        """List sessions for the current project, ordered newest first."""
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

            return store.list_sessions(project_id=project.id, limit=limit)
        finally:
            store.close()

    def get_session(
        self,
        session_id: str,
        start_dir: Path | str | None = None,
    ) -> Session:
        """Retrieve a specific session by ID.

        Raises:
            ProjectNotInitializedError: If no project is found.
            SessionNotFoundError: If the session ID does not exist.
        """
        start_path = Path(start_dir) if start_dir is not None else None
        project_root = self._locator.find_project_root(start_path)
        if project_root is None:
            raise ProjectNotInitializedError()

        db_path = self._locator.get_database_path(project_root)
        store = SQLiteStateStore(db_path, auto_migrate=False)
        try:
            session = store.get_session(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            return session
        finally:
            store.close()
