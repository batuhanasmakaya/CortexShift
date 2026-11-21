"""Selection of the CortexShift Session a handoff originates from.

Source selection reads only durable CortexShift session records. Provider transcript
history, hidden conversation storage, and provider-native session databases are never
consulted, and the outgoing provider is never contacted.
"""

from cortexshift.domain.errors import (
    NativeResumeError,
    NoSourceSessionError,
    SessionNotFoundError,
    SessionTaskMismatchError,
)
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import Session, SessionExitReason
from cortexshift.domain.task import Task
from cortexshift.ports.session_store import SessionStore


def _is_meaningful(session: Session) -> bool:
    """Whether a session represents real prior agent work.

    A session whose provider process never spawned contributed nothing, so it is not
    preferred as a handoff source while an earlier real session exists.
    """
    return session.exit_reason != SessionExitReason.SPAWN_FAILED


def select_source_session(
    store: SessionStore,
    task: Task,
    explicit_session_id: str | None = None,
    *,
    provider_id: ProviderId | None = None,
) -> Session:
    """Resolve the source Session for a handoff on the given active task.

    With no explicit override, the most recent meaningful session on the active task
    wins, falling back to the most recent session overall when every recorded session
    failed to spawn.

    Raises:
        SessionNotFoundError: If an explicitly requested session does not exist.
        SessionTaskMismatchError: If an explicit session belongs to a different task.
        NoSourceSessionError: If the active task has no prior session at all.
    """
    if explicit_session_id:
        session = store.get_session(explicit_session_id)
        if session is None:
            raise SessionNotFoundError(explicit_session_id)
        if session.task_id != task.id:
            raise SessionTaskMismatchError(explicit_session_id, task.id)
        if provider_id is not None and session.provider_id != provider_id:
            raise NativeResumeError("The selected CortexShift Session belongs to another provider.")
        return session

    sessions = [
        session
        for session in store.list_sessions(task_id=task.id, limit=None)
        if provider_id is None or session.provider_id == provider_id
    ]
    if not sessions:
        if provider_id is None:
            raise NoSourceSessionError()
        raise NoSourceSessionError(
            f"No previous {provider_id} Session exists for the active Task. "
            f"To start it with automatic context where supported, run "
            f"cortexshift start {provider_id} --task {task.id}."
        )

    for session in sessions:
        if _is_meaningful(session):
            return session

    return sessions[0]
