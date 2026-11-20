"""Project-wide reconciliation of unfinished managed invocations."""

import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.invocation_lock import probe_invocation_lock
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager, provider_guard_supported
from cortexshift.application.checkpoint_builder import CheckpointBuilder
from cortexshift.application.handoff_builder import derive_files_touched
from cortexshift.application.workspace import WorkspaceIdentity, WorkspaceStart, open_workspace
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.errors import GitProbeError, WorkspaceLockedError
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.mcp_binding import INVOCATION_VERIFIER_KEY
from cortexshift.domain.session import (
    PROVIDER_GUARDED_KEY,
    UNFINALIZED_SESSION_STATUSES,
    Session,
    SessionExitReason,
    SessionStatus,
)
from cortexshift.ports.repository import RepositoryInspector
from cortexshift.ports.workspace_lease import WorkspaceLeaseManager


class TaskRecovery(BaseModel):
    """Reconciliation and checkpoint evidence for one affected Task."""

    model_config = ConfigDict(frozen=True)

    task_id: str
    task_title: str
    session_ids: list[str]
    checkpoint_id: str | None = None


class RecoveryReport(BaseModel):
    """Recovery results for the verified project."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    task_id: str | None = None
    task_title: str | None = None
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
    examined_count: int = 0
    already_reconciled_count: int = 0
    live_session_ids: list[str] = Field(default_factory=list)
    uncertain_session_ids: list[str] = Field(default_factory=list)
    changed_session_ids: list[str] = Field(default_factory=list)
    tasks: list[TaskRecovery] = Field(default_factory=list)


def _ownership(session: Session, root: Path) -> str:
    """Classify a candidate while the workspace and provider preflight stay clear."""
    if (
        session.ended_at is not None
        or session.exit_reason is not None
        or session.exit_code is not None
    ):
        return "uncertain"

    verifier = session.metadata.get(INVOCATION_VERIFIER_KEY)
    if verifier is not None:
        # Windows invocation probes acquire a byte-range lock. Leave them to the
        # serialized MCP authorization path and rely on the workspace lease for exclusion.
        if (
            not provider_guard_supported()
            or not isinstance(verifier, str)
            or re.fullmatch(r"[0-9a-f]{64}", verifier) is None
        ):
            return "uncertain"
        try:
            if probe_invocation_lock(root, verifier):
                return "live"
        except OSError:
            return "uncertain"

    guarded = session.metadata.get(PROVIDER_GUARDED_KEY)
    # A valid C4 verifier on POSIX predates the explicit marker but was introduced
    # with the inherited provider guard. C4 publishes it before the interactive spawn.
    if guarded is True or (guarded is None and verifier is not None):
        return "interrupted"
    return "uncertain"


class RecoveryService:
    """Coordinates project-wide recovery under the exclusive workspace lease."""

    def __init__(
        self,
        inspector: RepositoryInspector | None = None,
        lease_manager: WorkspaceLeaseManager | None = None,
    ) -> None:
        self._inspector = inspector or GitRepositoryInspector()
        self._lease_manager = lease_manager or FileWorkspaceLeaseManager()

    def recover(
        self,
        start_dir: WorkspaceStart = None,
        *,
        dry_run: bool = False,
    ) -> RecoveryReport:
        """Preview or atomically reconcile each affected Task's unfinished Sessions."""
        workspace, store = open_workspace(start_dir)
        try:
            workspace.revalidate()
            lease = self._lease_manager.get_lease(workspace.root)
            if not lease.acquire():
                raise WorkspaceLockedError(lock_path=lease.lock_path)
            try:
                return self._recover_project(workspace, store, dry_run)
            finally:
                lease.release()
        finally:
            store.close()

    def _recover_project(
        self, workspace: WorkspaceIdentity, store: SQLiteStateStore, dry_run: bool
    ) -> RecoveryReport:
        project = workspace.project
        workspace.revalidate()
        candidates = [
            session
            for session in store.list_sessions(
                project_id=project.id, limit=None, statuses=UNFINALIZED_SESSION_STATUSES
            )
            if session.reconciled_at is None
        ]
        already_reconciled = store.count_reconciled_sessions(project.id)
        eligible: dict[str, list[Session]] = {}
        stale_ids: list[str] = []
        live_ids: list[str] = []
        uncertain_ids: list[str] = []
        for session in candidates:
            ownership = _ownership(session, workspace.root)
            if ownership == "interrupted":
                eligible.setdefault(session.task_id, []).append(session)
                stale_ids.append(session.id)
            elif ownership == "live":
                live_ids.append(session.id)
            else:
                uncertain_ids.append(session.id)

        inspection = self._inspector.inspect(project_root=workspace.root, project_id=project.id)
        workspace.revalidate()
        if inspection.status == RepositoryInspectionStatus.PROBE_ERROR:
            raise GitProbeError(
                inspection.diagnostic or "Git repository inspection failed during recovery."
            )

        tasks: list[TaskRecovery] = []
        snapshot = inspection.snapshot if inspection.is_ready else None
        snapshot_id: str | None = None
        for task_id, prepared in eligible.items():
            if dry_run:
                task = store.get_task(task_id)
                result = (
                    TaskRecovery(
                        task_id=task.id, task_title=task.title, session_ids=[s.id for s in prepared]
                    )
                    if task is not None and task.project_id == project.id
                    else None
                )
            else:
                result = self._reconcile_task(workspace, store, inspection, prepared, snapshot_id)
            if result is not None:
                tasks.append(result)
                if not dry_run and snapshot is not None:
                    snapshot_id = snapshot.id

        included_ids = {
            session_id for task_result in tasks for session_id in task_result.session_ids
        }
        reconciled = [session_id for session_id in stale_ids if session_id in included_ids]
        primary = tasks[0] if tasks else None
        active_id = store.get_active_task_id(project.id) if primary is None else None
        active = store.get_task(active_id) if active_id is not None else None
        metadata = (
            {"would_reconcile": bool(tasks), "would_create_checkpoint": bool(tasks)}
            if dry_run
            else {"reconciled_count": len(reconciled)}
        )
        return RecoveryReport(
            project_id=project.id,
            task_id=primary.task_id if primary else active.id if active else None,
            task_title=primary.task_title if primary else active.title if active else None,
            stale_session_ids=stale_ids,
            reconciled_session_ids=reconciled,
            checkpoint_id=primary.checkpoint_id if primary else None,
            git_snapshot_id=snapshot_id,
            repository_status=inspection.status,
            dirty=inspection.snapshot.dirty if inspection.snapshot else False,
            files_touched=derive_files_touched(inspection),
            dry_run=dry_run,
            metadata=metadata,
            examined_count=len(candidates) + already_reconciled,
            already_reconciled_count=already_reconciled,
            live_session_ids=live_ids,
            uncertain_session_ids=uncertain_ids,
            changed_session_ids=[sid for sid in stale_ids if sid not in included_ids],
            tasks=tasks,
        )

    @staticmethod
    def _reconcile_task(
        workspace: WorkspaceIdentity,
        store: SQLiteStateStore,
        inspection: RepositoryInspection,
        prepared: Sequence[Session],
        snapshot_id: str | None,
    ) -> TaskRecovery | None:
        workspace.revalidate()
        project = workspace.project
        task_id = prepared[0].task_id
        with store.write_transaction():
            task = store.get_task(task_id)
            if task is None or task.project_id != project.id:
                return None
            current = {
                session.id: session
                for session in store.list_sessions(
                    project_id=project.id,
                    task_id=task.id,
                    limit=None,
                    statuses=UNFINALIZED_SESSION_STATUSES,
                )
            }
            unchanged = [session for session in prepared if current.get(session.id) == session]
            if not unchanged:
                return None

            if snapshot_id is None and inspection.is_ready and inspection.snapshot is not None:
                store.save_snapshot(inspection.snapshot)
                snapshot_id = inspection.snapshot.id

            recovery_time = utc_now()
            recovered = [
                session.model_copy(
                    update={
                        "status": SessionStatus.INTERRUPTED,
                        "exit_reason": SessionExitReason.UNEXPECTED_TERMINATION,
                        "reconciled_at": recovery_time,
                    }
                )
                for session in unchanged
            ]
            for session in recovered:
                store.save_session(session)
            session_ids = [session.id for session in recovered]
            checkpoint = CheckpointBuilder.build(
                project=project,
                task=task,
                inspection=inspection,
                kind=CheckpointKind.RECOVERY,
                snapshot_id=snapshot_id,
                session=recovered[0],
                metadata={"reconciled_session_ids": session_ids},
            )
            store.save_checkpoint(checkpoint)
            return TaskRecovery(
                task_id=task.id,
                task_title=task.title,
                session_ids=session_ids,
                checkpoint_id=checkpoint.id,
            )
