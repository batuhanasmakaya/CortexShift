"""Selection of the CortexShift Session a handoff originates from.

Source selection reads only durable CortexShift session records. Provider transcript
history, hidden conversation storage, and provider-native session databases are never
consulted, and the outgoing provider is never contacted.
"""

from cortexshift.domain.errors import (
    NoSourceSessionError,
    SessionNotFoundError,
    SessionTaskMismatchError,
)
from cortexshift.domain.session import Session, SessionExitReason
from cortexshift.domain.task import Task
from cortexshift.ports.session_store import SessionStore

# How far back to look for a meaningful prior session on the active task.
_SOURCE_LOOKUP_LIMIT = 100


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
        return session

    sessions = store.list_sessions(task_id=task.id, limit=_SOURCE_LOOKUP_LIMIT)
    if not sessions:
        raise NoSourceSessionError()

    for session in sessions:
        if _is_meaningful(session):
            return session

    return sessions[0]
