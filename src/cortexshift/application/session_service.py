"""Application service for querying CortexShift session history."""

from cortexshift.application.locator import ProjectLocator
from cortexshift.application.workspace import WorkspaceStart, open_workspace
from cortexshift.domain.errors import SessionNotFoundError
from cortexshift.domain.session import Session


class SessionService:
    """Provides querying capabilities for agent execution sessions."""

    def __init__(self, project_locator: type[ProjectLocator] = ProjectLocator) -> None:
        self._locator = project_locator

    def list_sessions(
        self,
        start_dir: WorkspaceStart = None,
        limit: int = 20,
    ) -> list[Session]:
        """List sessions for the current project, ordered newest first."""
        workspace, store = open_workspace(start_dir, locator=self._locator)
        with store:
            return store.list_sessions(project_id=workspace.project.id, limit=limit)

    def get_session(
        self,
        session_id: str,
        start_dir: WorkspaceStart = None,
    ) -> Session:
        """Retrieve a specific session by ID.

        Raises:
            ProjectNotInitializedError: If no project is found.
            SessionNotFoundError: If the session ID does not exist.
        """
        _, store = open_workspace(start_dir, locator=self._locator)
        with store:
            session = store.get_session(session_id)
            if session is None:
                raise SessionNotFoundError(session_id)
            return session
