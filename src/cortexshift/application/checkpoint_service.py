"""Application service managing checkpoint creation, querying, and automatic session-end capture."""

import logging
from collections.abc import Callable
from typing import Any

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.checkpoint_builder import CheckpointBuilder
from cortexshift.application.workspace import WorkspaceIdentity, WorkspaceStart, open_workspace
from cortexshift.domain.checkpoint import (
    CheckpointKind,
    CheckpointRecord,
    CheckpointTestProvenance,
)
from cortexshift.domain.errors import (
    CheckpointNotFoundError,
    GitProbeError,
    NoActiveTaskError,
    ProjectConflictError,
    SessionNotFoundError,
    SessionTaskMismatchError,
    TaskNotFoundError,
)
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.session import Session, SessionExitReason
from cortexshift.domain.task import Task
from cortexshift.ports.repository import RepositoryInspector

logger = logging.getLogger(__name__)


def save_managed_checkpoint(
    store: SQLiteStateStore,
    workspace: WorkspaceIdentity,
    inspection: RepositoryInspection,
    authorize: Callable[[], tuple[Session, Task]],
    *,
    decisions: list[str] | None = None,
    test_summary: str | None = None,
    test_provenance: CheckpointTestProvenance = CheckpointTestProvenance.UNKNOWN,
    note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CheckpointRecord:
    """Persist an MCP checkpoint and its already-observed snapshot atomically."""
    with store.write_transaction():
        session, task = authorize()
        snapshot = inspection.snapshot
        snapshot_id = None
        if inspection.status == RepositoryInspectionStatus.READY and snapshot is not None:
            store.save_snapshot(snapshot)
            snapshot_id = snapshot.id

        record = CheckpointBuilder.build(
            project=workspace.project,
            task=task,
            inspection=inspection,
            kind=CheckpointKind.MANUAL,
            snapshot_id=snapshot_id,
            session=session,
            decisions=decisions,
            test_summary=test_summary,
            test_provenance=test_provenance,
            operator_note=note,
            metadata=metadata,
        )
        store.save_checkpoint(record)
        return record


class CheckpointService:
    """Coordinates checkpoint operations across Project, Task, Repository, and Store."""

    def __init__(
        self,
        inspector: RepositoryInspector | None = None,
    ) -> None:
        self._inspector = inspector or GitRepositoryInspector()

    def _resolve_context(
        self,
        start_dir: WorkspaceStart = None,
        task_id: str | None = None,
    ) -> tuple[WorkspaceIdentity, Task, SQLiteStateStore]:
        """Resolve project, selected task, and state store."""
        workspace, store = open_workspace(start_dir)
        project = workspace.project
        try:
            target_id = task_id if task_id is not None else store.get_active_task_id(project.id)
            if target_id is None:
                raise NoActiveTaskError()

            task = store.get_task(target_id)
            if task is None or task.project_id != project.id:
                if task_id is not None:
                    raise TaskNotFoundError(task_id)
                raise NoActiveTaskError()

            return workspace, task, store
        except Exception:
            store.close()
            raise

    def create_checkpoint(
        self,
        kind: CheckpointKind = CheckpointKind.MANUAL,
        session_id: str | None = None,
        decisions: list[str] | None = None,
        test_summary: str | None = None,
        test_provenance: CheckpointTestProvenance = CheckpointTestProvenance.REPORTED,
        note: str | None = None,
        start_dir: WorkspaceStart = None,
        metadata: dict[str, Any] | None = None,
        *,
        task_id: str | None = None,
    ) -> CheckpointRecord:
        """Create and persist a canonical Checkpoint without requiring the workspace lease.

        Manual checkpoints may be called from within a running provider session.
        Inspection is non-atomic and advisory.
        """
        workspace, task, store = self._resolve_context(start_dir, task_id)
        project = workspace.project
        try:
            # 1. Resolve session if requested or find latest for the selected task
            session: Session | None = None
            if session_id is not None:
                session = store.get_session(session_id)
                if session is None:
                    raise SessionNotFoundError(session_id)
                if session.task_id != task.id:
                    raise SessionTaskMismatchError(session_id, task.id)
            else:
                sessions = store.list_sessions(task_id=task.id, limit=10)
                # Prefer running or finished sessions over spawn-failed
                for candidate in sessions:
                    if candidate.exit_reason != SessionExitReason.SPAWN_FAILED:
                        session = candidate
                        break
                if session is None and sessions:
                    session = sessions[0]

            # 2. Live Git inspection
            workspace.revalidate()
            inspection = self._inspector.inspect(
                project_root=workspace.root,
                project_id=project.id,
            )

            workspace.revalidate()
            if inspection.status == RepositoryInspectionStatus.PROBE_ERROR:
                raise GitProbeError(
                    inspection.diagnostic
                    or "Git repository inspection failed; refusing to create a checkpoint "
                    "from an unreliable repository observation."
                )

            snapshot_id: str | None = None
            if (
                inspection.status == RepositoryInspectionStatus.READY
                and inspection.snapshot is not None
            ):
                store.save_snapshot(inspection.snapshot)
                snapshot_id = inspection.snapshot.id

            # 3. Build immutable checkpoint record
            record = CheckpointBuilder.build(
                project=project,
                task=task,
                inspection=inspection,
                kind=kind,
                snapshot_id=snapshot_id,
                session=session,
                decisions=decisions,
                test_summary=test_summary,
                test_provenance=test_provenance,
                operator_note=note,
                metadata=metadata,
            )

            # 4. Persist
            store.save_checkpoint(record)
            return record
        finally:
            store.close()

    def get_checkpoint(
        self,
        checkpoint_id: str,
        start_dir: WorkspaceStart = None,
    ) -> CheckpointRecord:
        """Retrieve a Checkpoint by its unique identifier."""
        _, store = open_workspace(start_dir)
        with store:
            checkpoint = store.get_checkpoint(checkpoint_id)
            if checkpoint is None:
                raise CheckpointNotFoundError(checkpoint_id)
            return checkpoint

    def list_checkpoints(
        self,
        task_id: str | None = None,
        limit: int | None = 20,
        start_dir: WorkspaceStart = None,
    ) -> list[CheckpointRecord]:
        """List checkpoints for the active task or project, newest first."""
        workspace, store = open_workspace(start_dir)
        project = workspace.project
        with store:
            resolved_task_id = task_id
            if resolved_task_id is None:
                resolved_task_id = store.get_active_task_id(project.id)

            return store.list_checkpoints(
                project_id=project.id,
                task_id=resolved_task_id,
                limit=limit,
            )

    def get_latest_checkpoint(
        self,
        task_id: str | None = None,
        start_dir: WorkspaceStart = None,
    ) -> CheckpointRecord | None:
        """Retrieve the newest checkpoint for the active task."""
        results = self.list_checkpoints(task_id=task_id, limit=1, start_dir=start_dir)
        return results[0] if results else None

    def capture_session_end_checkpoint(
        self,
        session: Session,
        store: SQLiteStateStore,
        workspace: WorkspaceIdentity | None = None,
    ) -> CheckpointRecord | None:
        """Safely capture an automatic SESSION_END checkpoint when a provider process finishes.

        This method never raises an exception out to the caller, preventing checkpoint
        failures from rewriting or failing an otherwise completed coding session.
        """
        try:
            task = store.get_task(session.task_id)
            if task is None:
                return None

            if workspace is None:
                workspace, _ = open_workspace(
                    store.db_path.parent.parent, exact_root=True, store_override=store
                )
            project = workspace.project
            if project.id != task.project_id:
                return None

            workspace.revalidate()
            inspection = self._inspector.inspect(
                project_root=workspace.root,
                project_id=project.id,
            )

            workspace.revalidate()
            if inspection.status == RepositoryInspectionStatus.PROBE_ERROR:
                # Safe warning: record capture warning in session metadata without rewriting status
                logger.warning(
                    "Session-end checkpoint capture skipped: git probe error for session %s",
                    session.id,
                )
                updated_meta = dict(session.metadata)
                updated_meta["checkpoint_capture_warning"] = "git_probe_error"
                store.save_session(session.model_copy(update={"metadata": updated_meta}))
                return None

            snapshot_id: str | None = None
            if (
                inspection.status == RepositoryInspectionStatus.READY
                and inspection.snapshot is not None
            ):
                store.save_snapshot(inspection.snapshot)
                snapshot_id = inspection.snapshot.id

            record = CheckpointBuilder.build(
                project=project,
                task=task,
                inspection=inspection,
                kind=CheckpointKind.SESSION_END,
                snapshot_id=snapshot_id,
                session=session,
                test_provenance=CheckpointTestProvenance.UNKNOWN,
            )

            store.save_checkpoint(record)
            return record
        except ProjectConflictError as exc:
            logger.warning("Session-end checkpoint skipped: %s", exc)
            return None
        except Exception as exc:
            logger.warning(
                "Failed to capture session-end checkpoint for session %s: %s",
                session.id,
                exc,
            )
            try:
                updated_meta = dict(session.metadata)
                updated_meta["checkpoint_capture_warning"] = str(exc)
                store.save_session(session.model_copy(update={"metadata": updated_meta}))
            except Exception:
                pass
            return None
