"""Integration tests verifying Git snapshot persistence, process durability, and isolation."""

import subprocess
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.repository_service import RepositoryService
from cortexshift.domain.errors import DatabaseStateError
from cortexshift.domain.git import GitSnapshot


def _setup_git_repo(path: Path) -> None:
    """Helper to initialize a git repo with a commit in path."""
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=path, check=True, capture_output=True)


def test_snapshot_lifecycle_and_reopen(tmp_path: Path) -> None:
    """Verify that a captured snapshot survives process recreation and instance discarding."""
    _setup_git_repo(tmp_path)

    # Initialize CortexShift project
    init_service = ProjectInitializationService()
    init_res = init_service.initialize(tmp_path, "lifecycle-project")
    assert init_res.already_initialized is False

    # 1. Capture a snapshot using first instance
    service_1 = RepositoryService()
    snap_1 = service_1.capture_snapshot(start_path=tmp_path)
    assert snap_1.branch == "main"
    assert snap_1.dirty is False

    # 2. Completely discard all instances and references
    del service_1
    del init_service

    # 3. Create brand new instances simulating a new process execution
    db_path = ProjectLocator.get_database_path(tmp_path)
    with SQLiteStateStore(db_path, auto_migrate=False) as store_2:
        service_2 = RepositoryService(store=store_2)
        history = service_2.list_snapshots(start_path=tmp_path)
        assert len(history) == 1
        restored = history[0]

        assert restored.id == snap_1.id
        assert restored.project_id == snap_1.project_id
        assert restored.branch == "main"
        assert restored.head_sha == snap_1.head_sha
        assert restored.dirty is False
        assert restored.staged_files == []
        assert restored.modified_files == []
        assert restored.captured_at == snap_1.captured_at


def test_snapshot_workspace_isolation(tmp_path: Path) -> None:
    """Verify two CortexShift projects never leak snapshots across workspaces."""
    workspace_a = tmp_path / "ws_a"
    workspace_b = tmp_path / "ws_b"
    workspace_a.mkdir()
    workspace_b.mkdir()

    _setup_git_repo(workspace_a)
    _setup_git_repo(workspace_b)

    init_service = ProjectInitializationService()
    proj_a = init_service.initialize(workspace_a, "project-a").project
    proj_b = init_service.initialize(workspace_b, "project-b").project

    service_a = RepositoryService()
    snap_a = service_a.capture_snapshot(start_path=workspace_a)

    service_b = RepositoryService()
    snap_b = service_b.capture_snapshot(start_path=workspace_b)

    # Check that A only sees snap_a and B only sees snap_b
    snapshots_a = service_a.list_snapshots(project_id=proj_a.id, start_path=workspace_a)
    snapshots_b = service_b.list_snapshots(project_id=proj_b.id, start_path=workspace_b)

    assert len(snapshots_a) == 1
    assert snapshots_a[0].id == snap_a.id
    assert snapshots_a[0].project_id == proj_a.id

    assert len(snapshots_b) == 1
    assert snapshots_b[0].id == snap_b.id
    assert snapshots_b[0].project_id == proj_b.id


def test_snapshot_foreign_key_enforcement(tmp_path: Path) -> None:
    """Verify persisting a snapshot for a nonexistent project raises DatabaseStateError."""
    db_file = tmp_path / "test_fk.sqlite3"
    with SQLiteStateStore(db_file, auto_migrate=True) as store:
        orphan_snap = GitSnapshot(
            project_id="proj_nonexistent_999",
            project_root=str(tmp_path),
            git_root=str(tmp_path),
            branch="main",
        )
        with pytest.raises(DatabaseStateError, match="Failed to persist Git snapshot"):
            store.save_snapshot(orphan_snap)


def test_live_vs_stored_behavior(tmp_path: Path) -> None:
    """Demonstrate that stored snapshots remain historical observations when repo changes."""
    _setup_git_repo(tmp_path)

    init_service = ProjectInitializationService()
    init_service.initialize(tmp_path, "live-vs-stored")

    service = RepositoryService()

    # 1. Capture clean snapshot
    snap_initial = service.capture_snapshot(start_path=tmp_path)
    assert snap_initial.dirty is False
    assert snap_initial.modified_files == []

    # 2. Modify working tree after capturing snapshot
    (tmp_path / "app.py").write_text("print('modified after snapshot')\n")
    (tmp_path / "untracked.txt").write_text("new file")

    # 3. Retrieve stored snapshot: MUST remain unchanged (dirty=False)
    stored_snap = service.get_snapshot(snap_initial.id, start_path=tmp_path)
    assert stored_snap is not None
    assert stored_snap.dirty is False
    assert stored_snap.modified_files == []
    assert stored_snap.untracked_files == []

    # 4. Perform live inspection: MUST reflect current reality (dirty=True)
    live_inspection = service.inspect_repository(start_path=tmp_path)
    assert live_inspection.is_ready is True
    assert live_inspection.snapshot is not None
    assert live_inspection.snapshot.dirty is True
    assert "app.py" in live_inspection.snapshot.modified_files
    assert "untracked.txt" in live_inspection.snapshot.untracked_files
