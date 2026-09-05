"""Shared provider session lifecycle used by both `run` and `switch`.

Extracted from ``RunService`` in Phase 5 so that launching a native provider and
tracking its CortexShift Session lifecycle exists in exactly one place. The launcher
assumes its caller has already resolved the project and task and already holds the
exclusive workspace lease, which keeps `switch` from nesting a second advisory lock
around the same workspace.
"""

import contextlib
from collections.abc import Callable
from typing import Any

from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.ports.process_runner import InteractiveProcessRunner
from cortexshift.ports.session_store import SessionStore


class ProviderSessionLauncher:
    """Creates, runs, and finalizes CortexShift Sessions for native provider processes."""

    def __init__(
        self,
        process_runner: InteractiveProcessRunner,
        store: SessionStore,
        checkpoint_service: Any | None = None,
    ) -> None:
        self._runner = process_runner
        self._store = store
        self._checkpoint_service = checkpoint_service

    def start_session(
        self,
        task_id: str,
        provider_id: ProviderId,
        native_session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        resumed_from_session_id: str | None = None,
    ) -> Session:
        """Create and persist a running Session immediately before provider work begins."""
        session = Session(
            task_id=task_id,
            provider_id=provider_id,
            native_session_id=native_session_id,
            resumed_from_session_id=resumed_from_session_id,
            status=SessionStatus.RUNNING,
            started_at=utc_now(),
            metadata=metadata or {},
        )
        self._store.save_session(session)
        return session

    def attach_native_session_id(self, session: Session, native_session_id: str) -> Session:
        """Bind a provider-native conversation identifier to an existing Session."""
        updated = session.model_copy(update={"native_session_id": native_session_id})
        self._store.save_session(updated)
        return updated

    def fail_session(
        self,
        session: Session,
        exit_reason: SessionExitReason,
        exit_code: int | None = None,
    ) -> Session:
        """Mark a Session failed before or instead of an interactive process run."""
        updated = session.model_copy(
            update={
                "status": SessionStatus.FAILED,
                "ended_at": utc_now(),
                "exit_reason": exit_reason,
                "exit_code": exit_code,
            }
        )
        self._store.save_session(updated)
        return updated

    def run(
        self,
        session: Session,
        launch_spec: LaunchSpecification,
        on_launch: Callable[[LaunchSpecification, Session], None] | None = None,
    ) -> Session:
        """Run the native provider process and finalize the Session lifecycle.

        Generic non-zero exits map strictly to `process_crashed`; CortexShift never infers
        quota exhaustion or rate limiting from an exit code. Spawn failures map to
        `spawn_failed` and re-raise so the caller can classify the operation as failed.
        """
        try:
            if on_launch:
                on_launch(launch_spec, session)

            exit_code = self._runner.run_interactive(
                argv=launch_spec.argv,
                cwd=launch_spec.cwd,
            )

            ended_at = utc_now()
            if exit_code == 0:
                session = session.model_copy(
                    update={
                        "status": SessionStatus.COMPLETED,
                        "ended_at": ended_at,
                        "exit_code": 0,
                        "exit_reason": SessionExitReason.NORMAL_COMPLETION,
                    }
                )
            elif exit_code in (130, -2):
                session = session.model_copy(
                    update={
                        "status": SessionStatus.INTERRUPTED,
                        "ended_at": ended_at,
                        "exit_code": 130,
                        "exit_reason": SessionExitReason.USER_INTERRUPTED,
                    }
                )
            else:
                session = session.model_copy(
                    update={
                        "status": SessionStatus.FAILED,
                        "ended_at": ended_at,
                        "exit_code": exit_code,
                        "exit_reason": SessionExitReason.PROCESS_CRASHED,
                    }
                )
        except KeyboardInterrupt:
            session = session.model_copy(
                update={
                    "status": SessionStatus.INTERRUPTED,
                    "ended_at": utc_now(),
                    "exit_code": 130,
                    "exit_reason": SessionExitReason.USER_INTERRUPTED,
                }
            )
        except Exception:
            session = session.model_copy(
                update={
                    "status": SessionStatus.FAILED,
                    "ended_at": utc_now(),
                    "exit_reason": SessionExitReason.SPAWN_FAILED,
                }
            )
            self._store.save_session(session)
            raise

        self._store.save_session(session)

        # Automatic session-end checkpoint capture for completed, failed, or interrupted runs
        if self._checkpoint_service is not None and hasattr(
            self._checkpoint_service, "capture_session_end_checkpoint"
        ):
            with contextlib.suppress(Exception):
                self._checkpoint_service.capture_session_end_checkpoint(
                    session=session,
                    store=self._store,
                )

        return session
