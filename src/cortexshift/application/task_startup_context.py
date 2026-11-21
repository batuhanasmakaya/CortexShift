"""Serve bounded pages of the canonical objective to the live startup invocation."""

from collections.abc import Callable

from pydantic import BaseModel

from cortexshift.application.handoff_builder import unique_checkpoint_decisions
from cortexshift.domain.checkpoint import CheckpointReference, CheckpointTestEvidence
from cortexshift.domain.errors import McpContextError
from cortexshift.domain.handoff import HandoffStatus
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import STARTUP_OBJECTIVE_SERVED_KEY, Session
from cortexshift.domain.status import ObjectiveRetrieval
from cortexshift.domain.task import Task
from cortexshift.ports.checkpoint_store import CheckpointStore
from cortexshift.ports.handoff_store import HandoffStore
from cortexshift.ports.session_store import SessionStore

OBJECTIVE_PAGE_CHARS = 4000


class TaskObjectivePage(BaseModel):
    task_id: str
    text: str
    offset: int
    next_offset: int | None


def objective_retrieval(session: Session, objective_length: int) -> ObjectiveRetrieval:
    served = session.metadata.get(STARTUP_OBJECTIVE_SERVED_KEY)
    if not isinstance(served, int):
        return ObjectiveRetrieval.NOT_REQUESTED
    if served == 0:
        return ObjectiveRetrieval.UNCONFIRMED
    if served < objective_length:
        return ObjectiveRetrieval.PARTIAL
    return ObjectiveRetrieval.COMPLETE


class HistoricalHandoff(BaseModel):
    id: str
    source_provider_id: ProviderId
    target_provider_id: ProviderId
    status: HandoffStatus
    operator_note: str | None


class TaskHistory(BaseModel):
    task_id: str
    latest_checkpoint: CheckpointReference | None
    important_decisions: list[str]
    historical_test_evidence: CheckpointTestEvidence | None
    latest_handoff: HistoricalHandoff | None


def read_task_history(
    task: Task, checkpoints: CheckpointStore, handoffs: HandoffStore
) -> TaskHistory:
    """Retrieve canonical Task history without retransmitting the objective."""
    records = handoffs.list_handoffs(project_id=task.project_id, task_id=task.id, limit=1)
    latest = records[0] if records else None
    return TaskHistory(
        task_id=task.id,
        latest_checkpoint=checkpoints.get_latest_checkpoint_reference(task.project_id, task.id),
        important_decisions=unique_checkpoint_decisions(
            checkpoints.list_checkpoint_decisions(task.project_id, task.id)
        ),
        historical_test_evidence=checkpoints.get_latest_checkpoint_test_evidence(
            task.project_id, task.id
        ),
        latest_handoff=HistoricalHandoff(
            id=latest.id,
            source_provider_id=latest.source_provider_id,
            target_provider_id=latest.target_provider_id,
            status=latest.status,
            operator_note=latest.payload.operator_note,
        )
        if latest
        else None,
    )


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
