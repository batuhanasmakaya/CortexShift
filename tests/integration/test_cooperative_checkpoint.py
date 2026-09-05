"""Integration test for cooperative milestone checkpoints created while workspace lease is held."""

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.cli.app import app
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task

runner = CliRunner()


def _seed(tmp_path: Path) -> tuple[Project, Task, Session]:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True
    )
    (tmp_path / "README.md").write_text("# Seed\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), check=True)

    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(db_file) as store:
        project = Project(name="Proj", repo_path=str(tmp_path))
        store.save_project(project)
        task = Task(project_id=project.id, title="Coop Task", objective="Coop Objective")
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)
        session = Session(
            task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.RUNNING
        )
        store.save_session(session)
    return project, task, session


def test_cooperative_checkpoint_while_lease_is_locked(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project, task, session = _seed(tmp_path)

    lease_mgr = FileWorkspaceLeaseManager()
    lease = lease_mgr.get_lease(tmp_path)
    # Simulate an active coding agent process holding the exclusive workspace lock
    assert lease.acquire() is True
    assert lease.is_locked() is True

    try:
        # CheckpointService can create a checkpoint without lock conflict
        service = CheckpointService()
        cp = service.create_checkpoint(
            decisions=["Decision while locked"],
            note="Agent milestone 1",
            start_dir=tmp_path,
        )
        assert cp.id.startswith("cp_")
        assert cp.kind == CheckpointKind.MANUAL
        assert cp.session_id == session.id
        assert cp.payload.decisions == ["Decision while locked"]

        # CLI command also succeeds without lock conflict
        cli_result = runner.invoke(
            app,
            ["checkpoint", "create", "-d", "Decision via CLI", "--json"],
        )
        assert cli_result.exit_code == 0
        cli_cp = json.loads(cli_result.stdout)
        assert cli_cp["kind"] == "manual"

        # Verify the lock is STILL held by the running agent!
        assert lease.is_locked() is True
    finally:
        lease.release()

    assert lease.is_locked() is False
