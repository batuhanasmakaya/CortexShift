"""Unit tests for cortexshift checkpoint CLI commands."""

import json
from pathlib import Path

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.cli.app import app
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task
from tests.cli_runner import AnsiFreeCliRunner

runner = AnsiFreeCliRunner()


def _setup_project(tmp_path: Path) -> tuple[Project, Task, Session]:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(db_file) as store:
        project = Project(name="CLIProj", repo_path=str(tmp_path))
        store.save_project(project)
        task = Task(project_id=project.id, title="CLI Task", objective="CLI Objective")
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)
        session = Session(
            task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.COMPLETED
        )
        store.save_session(session)
    return project, task, session


def test_checkpoint_create_human_and_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _setup_project(tmp_path)

    # Human output
    result = runner.invoke(
        app,
        [
            "checkpoint",
            "create",
            "-d",
            "Architecture decision 1",
            "-t",
            "12 passed",
            "-n",
            "Mid-sprint milestone",
        ],
    )
    assert result.exit_code == 0
    assert "Created checkpoint" in result.stdout
    assert "CLI Task" in result.stdout

    # JSON output
    json_result = runner.invoke(
        app,
        [
            "checkpoint",
            "create",
            "-d",
            "Architecture decision 2",
            "--json",
        ],
    )
    assert json_result.exit_code == 0
    data = json.loads(json_result.stdout)
    assert data["id"].startswith("cp_")
    assert data["kind"] == "manual"
    assert data["payload"]["decisions"] == ["Architecture decision 2"]


def test_checkpoint_list_show_and_latest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    _setup_project(tmp_path)

    # Create two checkpoints
    r1 = runner.invoke(app, ["checkpoint", "create", "-d", "D1", "--json"])
    cp1_id = json.loads(r1.stdout)["id"]
    r2 = runner.invoke(app, ["checkpoint", "create", "-d", "D2", "--json"])
    cp2_id = json.loads(r2.stdout)["id"]

    # List checkpoints
    list_res = runner.invoke(app, ["checkpoint", "list"])
    assert list_res.exit_code == 0
    assert cp1_id in list_res.stdout
    assert cp2_id in list_res.stdout

    # List JSON
    list_json_res = runner.invoke(app, ["checkpoint", "list", "--json"])
    assert list_json_res.exit_code == 0
    cps = json.loads(list_json_res.stdout)
    assert len(cps) == 2
    assert cps[0]["id"] == cp2_id  # reverse chronological

    # Show checkpoint
    show_res = runner.invoke(app, ["checkpoint", "show", cp1_id])
    assert show_res.exit_code == 0
    assert cp1_id in show_res.stdout
    assert "D1" in show_res.stdout

    # Show JSON
    show_json_res = runner.invoke(app, ["checkpoint", "show", cp1_id, "--json"])
    assert show_json_res.exit_code == 0
    assert json.loads(show_json_res.stdout)["id"] == cp1_id

    # Show missing
    missing_res = runner.invoke(app, ["checkpoint", "show", "cp_nonexistent"])
    assert missing_res.exit_code != 0

    # Latest checkpoint
    latest_res = runner.invoke(app, ["checkpoint", "latest"])
    assert latest_res.exit_code == 0
    assert cp2_id in latest_res.stdout
