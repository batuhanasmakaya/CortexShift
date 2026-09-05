"""Port defining persistence boundaries for CortexShift agent sessions."""

from typing import Protocol, runtime_checkable

from cortexshift.domain.session import Session


@runtime_checkable
class SessionStore(Protocol):
    """Abstract port for persisting and querying CortexShift agent execution sessions."""

    def save_session(self, session: Session) -> None:
        """Persist or update a Session record."""
        ...

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a Session by its stable identifier."""
        ...

    def list_sessions(
        self,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int | None = 20,
    ) -> list[Session]:
        """List sessions, ordered newest first."""
        ...
