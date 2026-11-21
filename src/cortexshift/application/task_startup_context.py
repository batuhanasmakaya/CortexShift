"""Serve bounded pages of the canonical objective to the live startup invocation."""

from collections.abc import Callable

from pydantic import BaseModel

from cortexshift.domain.errors import McpContextError
from cortexshift.domain.session import STARTUP_OBJECTIVE_SERVED_KEY, Session
from cortexshift.domain.task import Task
from cortexshift.ports.session_store import SessionStore

OBJECTIVE_PAGE_CHARS = 4000


class TaskObjectivePage(BaseModel):
    task_id: str
    text: str
    offset: int
    next_offset: int | None


def serve_task_objective(
    store: SessionStore,
    authorize: Callable[[], tuple[Session, Task]],
    offset: int,
) -> TaskObjectivePage:
    """Validate the binding and update its retrieval receipt in one transaction."""
    with store.write_transaction():
        session, task = authorize()
        served = session.metadata.get(STARTUP_OBJECTIVE_SERVED_KEY)
        if not isinstance(served, int):
            raise McpContextError("No Task startup context is bound to this invocation.")
        if not 0 <= offset <= served or offset >= len(task.objective):
            raise McpContextError("Start at offset 0 and follow the returned next_offset values.")

        page = task.objective[offset : offset + OBJECTIVE_PAGE_CHARS]
        end = offset + len(page)
        if end > served:
            store.save_session(
                session.model_copy(
                    update={"metadata": {**session.metadata, STARTUP_OBJECTIVE_SERVED_KEY: end}}
                )
            )
        return TaskObjectivePage(
            task_id=task.id,
            text=page,
            offset=offset,
            next_offset=end if end < len(task.objective) else None,
        )
