"""Unit tests for RecoveryService crash recovery, lease acquisition, and honest reconciliation."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.checkpoint_builder import CheckpointBuilder
from cortexshift.application.recovery_service import RecoveryReport, RecoveryService
from cortexshift.application.run_service import RunService
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.errors import WorkspaceLockedError
from cortexshift.domain.git import GitSnapshot, RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.mcp_binding import INVOCATION_VERIFIER_KEY
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import (
    PROVIDER_GUARDED_KEY,
    Session,
    SessionExitReason,
    SessionStatus,
)
from cortexshift.domain.task import Task, TaskStatus
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
        s = Session(
            task_id=task.id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.RUNNING,
            metadata={PROVIDER_GUARDED_KEY: True},
        )
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
            task_id=task.id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.INITIALIZING,
            metadata={PROVIDER_GUARDED_KEY: True},
        )
        s2 = Session(
            task_id=task.id,
            provider_id=PROVIDER_CODEX,
            status=SessionStatus.RUNNING,
            metadata={PROVIDER_GUARDED_KEY: True},
        )
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


def test_recovery_reconciles_session_on_inactive_task(tmp_path: Path) -> None:
    project, active = _seed(tmp_path)
    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3") as store:
        inactive = Task(project_id=project.id, title="Inactive", objective="Finish work")
        store.save_task(inactive)
        session = Session(
            task_id=inactive.id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.RUNNING,
            metadata={PROVIDER_GUARDED_KEY: True},
        )
        store.save_session(session)

    report = RecoveryService(inspector=MockInspector()).recover(tmp_path)

    assert report.reconciled_session_ids == [session.id]
    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3") as store:
        assert store.get_active_task_id(project.id) == active.id
        assert [cp.session_id for cp in store.list_checkpoints(task_id=inactive.id)] == [session.id]


def test_recovery_rolls_back_a_failed_session_update_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, task = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    session = Session(
        task_id=task.id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.RUNNING,
        metadata={PROVIDER_GUARDED_KEY: True},
    )
    with SQLiteStateStore(db_file) as store:
        store.save_session(session)

    service = RecoveryService(inspector=MockInspector())
    save_session = SQLiteStateStore.save_session

    def fail_after_update(store: SQLiteStateStore, updated: Session) -> None:
        save_session(store, updated)
        raise RuntimeError("injected interruption")

    with monkeypatch.context() as patch:
        patch.setattr(SQLiteStateStore, "save_session", fail_after_update)
        with pytest.raises(RuntimeError, match="injected interruption"):
            service.recover(tmp_path)

    with SQLiteStateStore(db_file) as store:
        assert store.get_session(session.id) == session
        assert store.list_checkpoints(project_id=project.id) == []
        assert store.list_snapshots(project_id=project.id) == []

    assert service.recover(tmp_path).reconciled_session_ids == [session.id]
    assert service.recover(tmp_path).reconciled_session_ids == []
    with SQLiteStateStore(db_file) as store:
        assert len(store.list_checkpoints(project_id=project.id)) == 1
        assert len(store.list_snapshots(project_id=project.id)) == 1


def _guarded(task: Task, **kwargs) -> Session:
    return Session(
        task_id=task.id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.RUNNING,
        metadata={PROVIDER_GUARDED_KEY: True},
        **kwargs,
    )


def test_project_recovery_without_active_task_preserves_history_and_task_state(tmp_path: Path):
    project, task = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    other = Task(
        project_id=project.id,
        title="Blocked task",
        objective="Finish work",
        requirements=["Requirement"],
        constraints=["Constraint"],
        status=TaskStatus.BLOCKED,
        completed_items=["Done"],
        current_work="Working",
        remaining_items=["Next"],
        known_issues=["Blocker"],
        metadata={"key": "value"},
    )
    foreign_project = Project(name="Unrelated", repo_path=str(tmp_path / "elsewhere"))
    foreign_task = Task(project_id=foreign_project.id, title="Foreign", objective="Other work")
    first, second, foreign = _guarded(task), _guarded(other), _guarded(foreign_task)
    completed = Session(
        task_id=other.id, provider_id=PROVIDER_CODEX, status=SessionStatus.COMPLETED
    )
    inspector = MockInspector()
    original_checkpoint = CheckpointBuilder.build(
        project,
        other,
        inspector.inspect(tmp_path, project.id),
        CheckpointKind.MANUAL,
        decisions=["Keep this decision"],
        test_summary="pytest failed",
    )
    with SQLiteStateStore(db_file) as store:
        store.set_active_task_id(project.id, None)
        store.save_task(other)
        store.save_project(foreign_project)
        store.save_task(foreign_task)
        for session in (first, second, foreign, completed):
            store.save_session(session)
        store.save_checkpoint(original_checkpoint)

    service = RecoveryService(inspector=inspector)
    preview = service.recover(tmp_path, dry_run=True)
    report = service.recover(tmp_path)
    assert preview.stale_session_ids == report.reconciled_session_ids == [second.id, first.id]
    assert preview.checkpoint_id is None and all(
        task.checkpoint_id is None for task in preview.tasks
    )
    assert report.examined_count == 2
    again = service.recover(tmp_path)
    assert again.reconciled_session_ids == [] and again.already_reconciled_count == 2
    with SQLiteStateStore(db_file) as store:
        assert store.get_schema_version() == 6
        assert store.list_sessions(project_id=foreign_project.id) == [foreign]
        assert store.get_session(completed.id) == completed
        assert store.get_active_task_id(project.id) is None
        assert store.get_task(task.id) == task
        assert store.get_task(other.id) == other
        assert store.get_checkpoint(original_checkpoint.id) == original_checkpoint
        assert len(store.list_snapshots(project_id=project.id)) == 1
        for result in report.tasks:
            assert result.checkpoint_id is not None
            checkpoint = store.get_checkpoint(result.checkpoint_id)
            assert checkpoint is not None and checkpoint.task_id == result.task_id
            assert checkpoint.session_id == result.session_ids[0]
            assert checkpoint.metadata["reconciled_session_ids"] == result.session_ids
            assert checkpoint.git_snapshot_id == report.git_snapshot_id


def test_failed_checkpoint_rolls_back_its_task_and_retry_keeps_prior_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, active = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    earlier = _guarded(active)
    inactive = Task(project_id=project.id, title="Inactive", objective="Finish other work")
    later = _guarded(inactive)
    with SQLiteStateStore(db_file) as store:
        store.save_task(inactive)
        store.save_session(earlier)
        store.save_session(later)

    save_checkpoint = SQLiteStateStore.save_checkpoint

    def fail_on_second_task(store, checkpoint):
        save_checkpoint(store, checkpoint)
        if checkpoint.task_id == active.id:
            raise RuntimeError("injected checkpoint failure")

    service = RecoveryService(inspector=MockInspector())
    with monkeypatch.context() as patch:
        patch.setattr(SQLiteStateStore, "save_checkpoint", fail_on_second_task)
        with pytest.raises(RuntimeError, match="injected checkpoint failure"):
            service.recover(tmp_path)

    with SQLiteStateStore(db_file) as store:
        assert store.get_session(earlier.id) == earlier
        (committed,) = store.list_checkpoints(project_id=project.id)
        assert committed.session_id == later.id
        assert len(store.list_snapshots(project_id=project.id)) == 1
    assert service.recover(tmp_path).reconciled_session_ids == [earlier.id]
    with SQLiteStateStore(db_file) as store:
        assert store.get_checkpoint(committed.id) == committed
        assert len(store.list_checkpoints(project_id=project.id)) == 2


def test_recovery_revalidates_session_and_preserves_both_terminal_outcomes(tmp_path: Path) -> None:
    project, task = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    finalizes, interrupted = _guarded(task), _guarded(task)
    with SQLiteStateStore(db_file) as store:
        store.save_session(finalizes)
        store.save_session(interrupted)

    finalized = finalizes.model_copy(
        update={
            "status": SessionStatus.COMPLETED,
            "ended_at": utc_now(),
            "exit_code": 0,
            "exit_reason": SessionExitReason.NORMAL_COMPLETION,
        }
    )

    class FinalizingInspector(MockInspector):
        def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
            # Interleave a separate SQLite connection between prepare and commit.
            with SQLiteStateStore(db_file) as store:
                store.save_session(finalized)
            return super().inspect(project_root, project_id)

    report = RecoveryService(inspector=FinalizingInspector()).recover(tmp_path)
    assert report.changed_session_ids == [finalized.id]
    assert report.reconciled_session_ids == [interrupted.id]
    with SQLiteStateStore(db_file) as store:
        assert store.get_session(finalized.id) == finalized
        reconciled = store.get_session(interrupted.id)
        store.save_session(finalized.model_copy(update={"id": interrupted.id}))
        assert store.get_session(interrupted.id) == reconciled
        (checkpoint,) = store.list_checkpoints(project_id=project.id)
        assert checkpoint.metadata["reconciled_session_ids"] == [interrupted.id]


@pytest.mark.parametrize("dry_run", [False, True])
def test_recovery_lease_excludes_other_recovery_and_launch(
    tmp_path: Path,
    dry_run: bool,
) -> None:
    _, task = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    interrupted = _guarded(task)
    with SQLiteStateStore(db_file) as store:
        store.save_session(interrupted)

    entered, release = Event(), Event()

    class BlockingInspector(MockInspector):
        def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
            entered.set()
            assert release.wait(timeout=10)
            return super().inspect(project_root, project_id)

    class FakeRunner:
        def run_interactive(self, argv, cwd, env=None) -> int:
            return 0

    recovery = RecoveryService(inspector=MockInspector())
    launch = RunService(
        process_runner=FakeRunner(), which_fn=lambda _: "/fake", is_tty_fn=lambda: True
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        primary = RecoveryService(inspector=BlockingInspector())
        pending = pool.submit(primary.recover, tmp_path, dry_run=dry_run)
        try:
            assert entered.wait(timeout=10)
            with pytest.raises(WorkspaceLockedError):
                recovery.recover(tmp_path)
            with pytest.raises(WorkspaceLockedError):
                launch.run("claude", start_dir=tmp_path)
            with SQLiteStateStore(db_file) as store:
                assert store.list_sessions(task_id=task.id) == [interrupted]
        finally:
            release.set()
        report: RecoveryReport = pending.result(timeout=10)
    assert report.reconciled_session_ids == [interrupted.id]
    assert recovery.recover(tmp_path).reconciled_session_ids == (
        [interrupted.id] if dry_run else []
    )
    fresh = launch.run("claude", start_dir=tmp_path)
    assert fresh.id != interrupted.id and fresh.status == SessionStatus.COMPLETED
    assert recovery.recover(tmp_path).reconciled_session_ids == []


@pytest.mark.parametrize("guard_supported", [True, False])
def test_recovery_distinguishes_live_and_uncertain_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    guard_supported: bool,
) -> None:
    _, task = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    def candidate(verifier: str | None = None) -> Session:
        return Session(
            task_id=task.id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.RUNNING,
            metadata={INVOCATION_VERIFIER_KEY: verifier} if verifier is not None else {},
        )

    legacy = candidate()
    live = candidate("a" * 64)
    expired = candidate("b" * 64)
    unreadable = candidate("c" * 64)
    invalid = candidate("../invalid")
    with SQLiteStateStore(db_file) as store:
        for session in (legacy, live, expired, unreadable, invalid):
            store.save_session(session)

    def probe(root: Path, verifier: str) -> bool:
        assert guard_supported
        if verifier == "c" * 64:
            raise OSError("cannot probe lock")
        return verifier == "a" * 64

    module = "cortexshift.application.recovery_service"
    monkeypatch.setattr(f"{module}.provider_guard_supported", lambda: guard_supported)
    monkeypatch.setattr(f"{module}.probe_invocation_lock", probe)
    report = RecoveryService(inspector=MockInspector()).recover(tmp_path)
    assert report.examined_count == 5
    assert report.live_session_ids == ([live.id] if guard_supported else [])
    assert report.reconciled_session_ids == ([expired.id] if guard_supported else [])
    expected_uncertain = [invalid.id, unreadable.id, legacy.id]
    if not guard_supported:
        expected_uncertain[2:2] = [expired.id, live.id]
    assert report.uncertain_session_ids == expected_uncertain
    with SQLiteStateStore(db_file) as store:
        assert store.get_session(live.id) == live
        assert store.get_session(unreadable.id) == unreadable
        assert store.get_session(legacy.id) == legacy
