"""Application service for crash recovery and stale session reconciliation."""

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.checkpoint_builder import CheckpointBuilder
from cortexshift.application.handoff_builder import derive_files_touched
from cortexshift.application.locator import ProjectLocator
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.errors import (
    GitProbeError,
    NoActiveTaskError,
    ProjectNotInitializedError,
    WorkspaceLockedError,
)
from cortexshift.domain.git import RepositoryInspectionStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.session import SessionExitReason, SessionStatus
from cortexshift.ports.repository import RepositoryInspector
from cortexshift.ports.workspace_lease import WorkspaceLeaseManager


class RecoveryReport(BaseModel):
    """Structured representation of crash recovery results."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    task_id: str
    task_title: str
    stale_session_ids: list[str] = Field(default_factory=list)
    reconciled_session_ids: list[str] = Field(default_factory=list)
    checkpoint_id: str | None = None
    git_snapshot_id: str | None = None
    repository_status: RepositoryInspectionStatus
    dirty: bool = False
    files_touched: list[str] = Field(default_factory=list)
    dry_run: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RecoveryService:
    """Coordinates crash recovery, lease acquisition, and session reconciliation."""

    def __init__(
        self,
        inspector: RepositoryInspector | None = None,
        lease_manager: WorkspaceLeaseManager | None = None,
    ) -> None:
        self._inspector = inspector or GitRepositoryInspector()
        self._lease_manager = lease_manager or FileWorkspaceLeaseManager()

    def recover(
        self,
        start_dir: Path | str | None = None,
        *,
        dry_run: bool = False,
    ) -> RecoveryReport:
        """Reconcile interrupted orchestration sessions and capture a Recovery Checkpoint.

        Requires an exclusive workspace lease to verify no active CortexShift provider
        process currently owns the workspace.
        """
        start_path = Path(start_dir) if start_dir is not None else None
        project_root = ProjectLocator.find_project_root(start_path)
        if project_root is None:
            raise ProjectNotInitializedError()

        db_path = ProjectLocator.get_database_path(project_root)
        store = SQLiteStateStore(db_path, auto_migrate=False)

        try:
            project = store.get_default_project()
            if project is None:
                raise ProjectNotInitializedError()

            active_task_id = store.get_active_task_id(project.id)
            if not active_task_id:
                raise NoActiveTaskError()

            task = store.get_task(active_task_id)
            if task is None:
                raise NoActiveTaskError()

            lease = self._lease_manager.get_lease(Path(project.repo_path))

            if dry_run:
                # Probe lease non-destructively
                if not lease.acquire():
                    raise WorkspaceLockedError(lock_path=lease.lock_path)
                lease.release()

                # Find stale sessions
                sessions = store.list_sessions(task_id=task.id, limit=None)
                stale_sessions = [
                    s
                    for s in sessions
                    if s.status in (SessionStatus.INITIALIZING, SessionStatus.RUNNING)
                ]
                stale_ids = [s.id for s in stale_sessions]

                inspection = self._inspector.inspect(
                    project_root=Path(project.repo_path),
                    project_id=project.id,
                )
                if inspection.status == RepositoryInspectionStatus.PROBE_ERROR:
                    raise GitProbeError(
                        inspection.diagnostic
                        or "Git repository inspection failed during recovery probe."
                    )

                dirty = inspection.snapshot.dirty if inspection.snapshot else False
                files_touched = derive_files_touched(inspection)

                return RecoveryReport(
                    project_id=project.id,
                    task_id=task.id,
                    task_title=task.title,
                    stale_session_ids=stale_ids,
                    reconciled_session_ids=stale_ids,
                    checkpoint_id=None,
                    git_snapshot_id=None,
                    repository_status=inspection.status,
                    dirty=dirty,
                    files_touched=files_touched,
                    dry_run=True,
                    metadata={
                        "would_reconcile": bool(stale_sessions),
                        "would_create_checkpoint": bool(stale_sessions),
                    },
                )

            # Non-dry-run: acquire lease
            if not lease.acquire():
                raise WorkspaceLockedError(lock_path=lease.lock_path)

            try:
                sessions = store.list_sessions(task_id=task.id, limit=None)
                stale_sessions = [
                    s
                    for s in sessions
                    if s.status in (SessionStatus.INITIALIZING, SessionStatus.RUNNING)
                ]
                stale_ids = [s.id for s in stale_sessions]

                inspection = self._inspector.inspect(
                    project_root=Path(project.repo_path),
                    project_id=project.id,
                )
                if inspection.status == RepositoryInspectionStatus.PROBE_ERROR:
                    raise GitProbeError(
                        inspection.diagnostic or "Git repository inspection failed during recovery."
                    )

                dirty = inspection.snapshot.dirty if inspection.snapshot else False
                files_touched = derive_files_touched(inspection)

                if not stale_sessions:
                    # Predictable no-op when no stale sessions exist
                    return RecoveryReport(
                        project_id=project.id,
                        task_id=task.id,
                        task_title=task.title,
                        stale_session_ids=[],
                        reconciled_session_ids=[],
                        checkpoint_id=None,
                        git_snapshot_id=None,
                        repository_status=inspection.status,
                        dirty=dirty,
                        files_touched=files_touched,
                        dry_run=False,
                        metadata={"message": "No stale sessions found to recover."},
                    )

                # Save Git snapshot if ready
                snapshot_id: str | None = None
                if (
                    inspection.status == RepositoryInspectionStatus.READY
                    and inspection.snapshot is not None
                ):
                    store.save_snapshot(inspection.snapshot)
                    snapshot_id = inspection.snapshot.id

                # Recovery Checkpoint associates with newest stale session
                primary_stale = stale_sessions[0]
                checkpoint = CheckpointBuilder.build(
                    project=project,
                    task=task,
                    inspection=inspection,
                    kind=CheckpointKind.RECOVERY,
                    snapshot_id=snapshot_id,
                    session=primary_stale,
                    metadata={"reconciled_session_ids": stale_ids},
                )
                store.save_checkpoint(checkpoint)

                # Reconcile all stale sessions
                recovery_time = utc_now()
                reconciled_ids: list[str] = []
                for s in stale_sessions:
                    reconciled = s.model_copy(
                        update={
                            "status": SessionStatus.INTERRUPTED,
                            "exit_reason": SessionExitReason.UNEXPECTED_TERMINATION,
                            "reconciled_at": recovery_time,
                        }
                    )
                    store.save_session(reconciled)
                    reconciled_ids.append(reconciled.id)

                return RecoveryReport(
                    project_id=project.id,
                    task_id=task.id,
                    task_title=task.title,
                    stale_session_ids=stale_ids,
                    reconciled_session_ids=reconciled_ids,
                    checkpoint_id=checkpoint.id,
                    git_snapshot_id=snapshot_id,
                    repository_status=inspection.status,
                    dirty=dirty,
                    files_touched=files_touched,
                    dry_run=False,
                    metadata={"reconciled_count": len(reconciled_ids)},
                )
            finally:
                lease.release()
        finally:
            store.close()
