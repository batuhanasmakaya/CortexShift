"""Unit tests for RecoveryService crash recovery, lease acquisition, and honest reconciliation."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.recovery_service import RecoveryService
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.errors import WorkspaceLockedError
from cortexshift.domain.git import GitSnapshot, RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.ports.repository import RepositoryInspector


class MockInspector(RepositoryInspector):
    def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
        snapshot = GitSnapshot(
            project_id=project_id,
            project_root=str(project_root),
            git_root=str(project_root),
            branch="main",
            dirty=True,
            modified_files=["crash_file.py"],
        )
        return RepositoryInspection(
            project_root=str(project_root),
            git_available=True,
            status=RepositoryInspectionStatus.READY,
            snapshot=snapshot,
        )


def _seed(tmp_path: Path) -> tuple[Project, Task]:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(db_file) as store:
        project = Project(name="TestProj", repo_path=str(tmp_path))
        store.save_project(project)
        task = Task(project_id=project.id, title="Task A", objective="Objective A")
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)
    return project, task


def test_recovery_blocks_when_workspace_lease_held(tmp_path: Path) -> None:
    project, task = _seed(tmp_path)
    lease_mgr = FileWorkspaceLeaseManager()
    lease = lease_mgr.get_lease(tmp_path)
    assert lease.acquire() is True

    try:
        service = RecoveryService(inspector=MockInspector(), lease_manager=lease_mgr)
        with pytest.raises(WorkspaceLockedError):
            service.recover(start_dir=tmp_path)
    finally:
        lease.release()


def test_recovery_no_stale_sessions_returns_clean_report(tmp_path: Path) -> None:
    project, task = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        # Completed session is not stale
        s = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.COMPLETED)
        store.save_session(s)

    service = RecoveryService(inspector=MockInspector())
    report = service.recover(start_dir=tmp_path)

    assert report.stale_session_ids == []
    assert report.reconciled_session_ids == []
    assert report.checkpoint_id is None
    assert report.dry_run is False


def test_recovery_dry_run_leaves_state_unmutated(tmp_path: Path) -> None:
    project, task = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        s = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.RUNNING)
        store.save_session(s)

    service = RecoveryService(inspector=MockInspector())
    report = service.recover(start_dir=tmp_path, dry_run=True)

    assert report.dry_run is True
    assert report.stale_session_ids == [s.id]
    assert report.reconciled_session_ids == [s.id]
    assert report.checkpoint_id is None

    # DB remains unmutated
    with SQLiteStateStore(db_file) as store:
        reloaded = store.get_session(s.id)
        assert reloaded is not None
        assert reloaded.status == SessionStatus.RUNNING
        assert reloaded.reconciled_at is None
        assert store.list_checkpoints(task_id=task.id) == []


def test_recovery_honest_reconciliation_and_recovery_checkpoint(tmp_path: Path) -> None:
    project, task = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        s1 = Session(
            task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.INITIALIZING
        )
        s2 = Session(task_id=task.id, provider_id=PROVIDER_CODEX, status=SessionStatus.RUNNING)
        store.save_session(s1)
        store.save_session(s2)

    service = RecoveryService(inspector=MockInspector())
    report = service.recover(start_dir=tmp_path)

    assert report.dry_run is False
    assert set(report.stale_session_ids) == {s1.id, s2.id}
    assert set(report.reconciled_session_ids) == {s1.id, s2.id}
    assert report.checkpoint_id is not None
    assert report.git_snapshot_id is not None
    assert report.dirty is True
    assert report.files_touched == ["crash_file.py"]

    with SQLiteStateStore(db_file) as store:
        # Check sessions honestly reconciled
        for s_id in (s1.id, s2.id):
            session = store.get_session(s_id)
            assert session is not None
            assert session.status == SessionStatus.INTERRUPTED
            assert session.exit_reason == SessionExitReason.UNEXPECTED_TERMINATION
            assert session.ended_at is None  # Never fabricated!
            assert session.reconciled_at is not None

        # Check recovery checkpoint
        cp = store.get_checkpoint(report.checkpoint_id)
        assert cp is not None
        assert cp.kind == CheckpointKind.RECOVERY
        assert cp.session_id == s2.id  # Newest stale session
        assert cp.payload.files_touched == ["crash_file.py"]
