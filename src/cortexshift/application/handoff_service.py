"""Application service for querying persisted canonical handoff history."""

from collections.abc import Callable

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.handoff_renderer import HandoffRenderer
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.workspace import WorkspaceStart, open_workspace
from cortexshift.domain.errors import HandoffNotFoundError, McpContextError
from cortexshift.domain.handoff import HandoffRecord, HandoffStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task
from cortexshift.ports.handoff_delivery import HandoffDeliveryStrategy


class HandoffService:
    """Provides read access to durable handoff records for the current project."""

    def __init__(self, project_locator: type[ProjectLocator] = ProjectLocator) -> None:
        self._locator = project_locator

    def _open(self, start_dir: WorkspaceStart) -> tuple[str, SQLiteStateStore]:
        """Resolve the initialized project and open its handoff store."""
        workspace, store = open_workspace(start_dir, locator=self._locator)
        return workspace.project.id, store

    def list_handoffs(
        self,
        start_dir: WorkspaceStart = None,
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
        start_dir: WorkspaceStart = None,
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


def serve_bound_handoff(
    store: SQLiteStateStore,
    authorize: Callable[[], tuple[Session, Task]],
) -> str:
    """Return the exact invocation's bounded handoff and record its first retrieval."""
    with store.write_transaction():
        session, task = authorize()
        handoff_id = session.metadata.get("handoff_id")
        handoff = store.get_handoff(handoff_id) if isinstance(handoff_id, str) else None
        if (
            handoff is None
            or handoff.project_id != task.project_id
            or handoff.task_id != task.id
            or handoff.target_session_id != session.id
            or handoff.target_provider_id != session.provider_id
            or handoff.status == HandoffStatus.FAILED
            or handoff.metadata.get("delivery_strategy")
            != HandoffDeliveryStrategy.MCP_HANDOFF_RETRIEVAL.value
        ):
            raise McpContextError("No MCP handoff is bound to this invocation.")

        budget = handoff.metadata.get("context_max_bytes")
        if not isinstance(budget, int):
            raise McpContextError("The bound handoff has no valid UTF-8 budget.")
        rendered = HandoffRenderer(max_bytes=budget).render(handoff.payload, handoff.id)
        if handoff.status == HandoffStatus.PREPARED:
            store.update_handoff_delivery(
                handoff.id, status=HandoffStatus.DELIVERED, delivered_at=utc_now()
            )
        return rendered.text
