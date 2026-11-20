"""Synthetic live invocations for tests that exercise MCP without a provider runner."""

import secrets
from contextlib import ExitStack
from pathlib import Path

from pydantic import SecretStr

from cortexshift.adapters.invocation_lock import hold_invocation
from cortexshift.domain.mcp_binding import INVOCATION_VERIFIER_KEY, invocation_verifier
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.ports.session_store import SessionStore


class LiveMcpInvocations:
    def __init__(self, locks: ExitStack) -> None:
        self._locks = locks

    def open(self, root: Path, store: SessionStore, session: Session) -> SecretStr:
        assert session.status == SessionStatus.RUNNING and session.ended_at is None
        token = SecretStr(secrets.token_hex(32))
        verifier = invocation_verifier(token)
        self._locks.enter_context(hold_invocation(root, verifier))
        store.save_session(
            session.model_copy(
                update={"metadata": {**session.metadata, INVOCATION_VERIFIER_KEY: verifier}}
            )
        )
        return token

    def authorize(self, context: McpExecutionContext, store: SessionStore) -> McpExecutionContext:
        assert context.session_id is not None
        session = store.get_session(context.session_id)
        assert session is not None
        token = self.open(context.project_root, store, session)
        return context.model_copy(update={"invocation_token": token})
