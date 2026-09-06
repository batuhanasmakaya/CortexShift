"""Unit tests for cortexshift recover CLI command."""

import json
from pathlib import Path

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.cli.app import app
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from tests.cli_runner import AnsiFreeCliRunner

runner = AnsiFreeCliRunner()


def _setup_project(tmp_path: Path) -> tuple[Project, Task]:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(db_file) as store:
        project = Project(name="CLIProj", repo_path=str(tmp_path))
        store.save_project(project)
        task = Task(project_id=project.id, title="Recover Task", objective="Recover Objective")
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)
    return project, task


def test_recover_no_stale_sessions(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _setup_project(tmp_path)

    result = runner.invoke(app, ["recover"])
    assert result.exit_code == 0
    assert "No stale running or initializing sessions found" in result.stdout

    json_res = runner.invoke(app, ["recover", "--json"])
    assert json_res.exit_code == 0
    data = json.loads(json_res.stdout)
    assert data["stale_session_ids"] == []
    assert data["reconciled_session_ids"] == []


def test_recover_dry_run_and_real_recovery(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project, task = _setup_project(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    # Create a stale running session
    with SQLiteStateStore(db_file) as store:
        stale_sess = Session(
            task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.RUNNING
        )
        store.save_session(stale_sess)

    # 1. Dry run
    dry_res = runner.invoke(app, ["recover", "--dry-run"])
    assert dry_res.exit_code == 0
    assert "Dry Run Preview" in dry_res.stdout
    assert stale_sess.id in dry_res.stdout

    # Verify session remains RUNNING
    with SQLiteStateStore(db_file) as store:
        s = store.get_session(stale_sess.id)
        assert s is not None and s.status == SessionStatus.RUNNING

    # 2. Real recovery with JSON output
    rec_res = runner.invoke(app, ["recover", "--json"])
    assert rec_res.exit_code == 0
    report = json.loads(rec_res.stdout)
    assert report["dry_run"] is False
    assert report["stale_session_ids"] == [stale_sess.id]
    assert report["reconciled_session_ids"] == [stale_sess.id]
    assert report["checkpoint_id"].startswith("cp_")

    # Verify session reconciled in DB
    with SQLiteStateStore(db_file) as store:
        s = store.get_session(stale_sess.id)
        assert s is not None
        assert s.status == SessionStatus.INTERRUPTED
        assert s.exit_reason == SessionExitReason.UNEXPECTED_TERMINATION
        assert s.ended_at is None
        assert s.reconciled_at is not None

        cp = store.get_checkpoint(report["checkpoint_id"])
        assert cp is not None
        assert cp.kind.value == "recovery"


def test_recover_fails_when_lease_held(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _setup_project(tmp_path)

    lease_mgr = FileWorkspaceLeaseManager()
    lease = lease_mgr.get_lease(tmp_path)
    assert lease.acquire() is True

    try:
        res = runner.invoke(app, ["recover"])
        assert res.exit_code != 0
        assert (
            "locked" in res.stdout.lower()
            or "lease" in res.stdout.lower()
            or "lock" in res.stderr.lower()
        )
    finally:
        lease.release()
