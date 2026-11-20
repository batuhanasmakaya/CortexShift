"""Validate managed MCP writes against the canonical Session and launcher lifetime."""

from hmac import compare_digest
from typing import Protocol

from pydantic import SecretStr

from cortexshift.adapters.invocation_lock import invocation_is_live
from cortexshift.application.workspace import WorkspaceIdentity
from cortexshift.domain.errors import McpReadOnlyError
from cortexshift.domain.mcp_binding import INVOCATION_VERIFIER_KEY, invocation_verifier
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.ports.session_store import SessionStore
from cortexshift.ports.state_store import StateStore


class InvocationStateStore(StateStore, SessionStore, Protocol):
    """State needed to authorize a managed provider invocation."""


def require_active_invocation(
    workspace: WorkspaceIdentity,
    store: InvocationStateStore,
    *,
    session_id: str | None,
    task_id: str | None,
    provider_id: ProviderId | None,
    token: SecretStr | None,
) -> tuple[Session, Task]:
    """Check the exact binding. A mutation calls this inside its write transaction."""
    workspace.revalidate()
    expired = McpReadOnlyError("Managed MCP write authority has expired or is unavailable.")
    if session_id is None or task_id is None or provider_id is None or token is None:
        raise expired

    session = store.get_session(session_id)
    if (
        session is None
        or session.task_id != task_id
        or session.provider_id != provider_id
        or session.status != SessionStatus.RUNNING
        or session.ended_at is not None
        or session.reconciled_at is not None
    ):
        raise expired

    task = store.get_task(task_id)
    if task is None or task.project_id != workspace.project.id:
        raise expired

    expected = session.metadata.get(INVOCATION_VERIFIER_KEY)
    actual = invocation_verifier(token)
    if (
        not isinstance(expected, str)
        or not compare_digest(expected.encode(), actual.encode())
        or not invocation_is_live(workspace.root, actual)
    ):
        raise expired
    return session, task
