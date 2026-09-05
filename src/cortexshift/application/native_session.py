"""Exact resume selection from canonical CortexShift records only."""

from cortexshift.domain.errors import (
    NativeResumeError,
    SessionNotFoundError,
    SessionTaskMismatchError,
)
from cortexshift.domain.native_session import NativeSessionCapabilities, valid_native_id
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.ports.session_store import SessionStore


def is_native_resumable(session: Session, capabilities: NativeSessionCapabilities) -> bool:
    """Require a known ID and evidence that the provider process actually launched.

    An allocated ID on an unfinished or spawn-failed invocation is not enough.
    Exit evidence remains valid even when a launched process crashed or was interrupted.
    """
    return (
        capabilities.supports_exact_resume
        and valid_native_id(session.native_session_id)
        and session.status
        in (SessionStatus.COMPLETED, SessionStatus.INTERRUPTED, SessionStatus.FAILED)
        and session.exit_code is not None
        and session.exit_reason != SessionExitReason.SPAWN_FAILED
    )


def select_native_session(
    store: SessionStore,
    task_id: str,
    provider_id: ProviderId,
    capabilities: NativeSessionCapabilities,
    explicit_session_id: str | None = None,
) -> Session | None:
    if explicit_session_id is not None:
        session = store.get_session(explicit_session_id)
        if session is None:
            raise SessionNotFoundError(explicit_session_id)
        if session.task_id != task_id:
            raise SessionTaskMismatchError(explicit_session_id, task_id)
        if session.provider_id != provider_id:
            raise NativeResumeError("The selected CortexShift Session belongs to another provider.")
        if not is_native_resumable(session, capabilities):
            raise NativeResumeError("The selected CortexShift Session is not exactly resumable.")
        return session

    return next(
        (
            session
            for session in store.list_sessions(task_id=task_id, limit=None)
            if session.provider_id == provider_id and is_native_resumable(session, capabilities)
        ),
        None,
    )


def native_capabilities(adapter: object) -> NativeSessionCapabilities:
    """Unsupported adapters honestly advertise no native continuity."""
    from cortexshift.ports.native_session import ProviderNativeSessionAdapter

    if isinstance(adapter, ProviderNativeSessionAdapter):
        return adapter.get_native_capabilities()
    return NativeSessionCapabilities()
