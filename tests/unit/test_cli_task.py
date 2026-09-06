"""Unit tests for `cortexshift task` command group."""

import json
from pathlib import Path

import pytest

from cortexshift.cli.app import app
from tests.cli_runner import AnsiFreeCliRunner

runner = AnsiFreeCliRunner()


@pytest.fixture
def initialized_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--name", "TaskTestProj"])
    assert result.exit_code == 0
    return tmp_path


def test_task_help() -> None:
    """Verify `cortexshift task --help` displays all subcommands."""
    result = runner.invoke(app, ["task", "--help"])
    assert result.exit_code == 0
    assert "start" in result.stdout
    assert "list" in result.stdout
    assert "show" in result.stdout
    assert "activate" in result.stdout
    assert "complete" in result.stdout
    assert "update" in result.stdout


def test_task_start_and_show_active(initialized_dir: Path) -> None:
    """Verify `task start` creates active task and `task show` displays it."""
    start_res = runner.invoke(
        app,
        [
            "task",
            "start",
            "--title",
            "Implement Auth",
            "--objective",
            "Support session tokens",
            "--requirement",
            "Must be secure",
            "--constraint",
            "No plain text",
        ],
    )
    assert start_res.exit_code == 0
    assert "Started task" in start_res.stdout
    assert "Implement Auth" in start_res.stdout

    # Show active task
    show_res = runner.invoke(app, ["task", "show"])
    assert show_res.exit_code == 0
    assert "Implement Auth" in show_res.stdout
    assert "Support session tokens" in show_res.stdout
    assert "Must be secure" in show_res.stdout
    assert "No plain text" in show_res.stdout


def test_task_list_human_and_json(initialized_dir: Path) -> None:
    """Verify `task list` and `task list --json`."""
    runner.invoke(app, ["task", "start", "-t", "Task 1", "-o", "Obj 1"])
    runner.invoke(app, ["task", "start", "-t", "Task 2", "-o", "Obj 2"])

    # Human table
    list_res = runner.invoke(app, ["task", "list"])
    assert list_res.exit_code == 0
    assert "Task 1" in list_res.stdout
    assert "Task 2" in list_res.stdout

    # JSON list
    json_res = runner.invoke(app, ["task", "list", "--json"])
    assert json_res.exit_code == 0
    data = json.loads(json_res.stdout)
    assert isinstance(data, list)
    assert len(data) == 2
    assert data[0]["title"] == "Task 1"
    assert data[0]["is_active"] is False
    assert data[1]["title"] == "Task 2"
    assert data[1]["is_active"] is True
    assert data[0]["status"] == "in_progress"


def test_task_start_positional_and_repeatable_options(initialized_dir: Path) -> None:
    """Verify task start with positional title, explicit title, and repeatable options."""
    # Positional title
    pos_res = runner.invoke(
        app,
        ["task", "start", "Positional Title", "--objective", "Positional Obj"],
    )
    assert pos_res.exit_code == 0
    assert "Started task" in pos_res.stdout

    # Explicit title with repeatable requirements and constraints
    exp_res = runner.invoke(
        app,
        [
            "task",
            "start",
            "--title",
            "Canonical Context Task",
            "--objective",
            "Preserve exact context",
            "--requirement",
            "Req A",
            "--requirement",
            "Req B",
            "--constraint",
            "Const A",
            "--constraint",
            "Const B",
        ],
    )
    assert exp_res.exit_code == 0
    assert "Canonical Context Task" in exp_res.stdout

    # Verify through JSON show
    show_res = runner.invoke(app, ["task", "show", "--json"])
    assert show_res.exit_code == 0
    data = json.loads(show_res.stdout)
    assert data["title"] == "Canonical Context Task"
    assert data["objective"] == "Preserve exact context"
    assert data["requirements"] == ["Req A", "Req B"]
    assert data["constraints"] == ["Const A", "Const B"]
    assert data["is_active"] is True

    # Start with --no-set-active
    bg_res = runner.invoke(
        app,
        [
            "task",
            "start",
            "--title",
            "Background Task",
            "--objective",
            "Do not activate",
            "--no-set-active",
        ],
    )
    assert bg_res.exit_code == 0
    # Active task should still be Canonical Context Task
    active_res = runner.invoke(app, ["task", "show", "--json"])
    assert json.loads(active_res.stdout)["title"] == "Canonical Context Task"


def test_task_list_status_filter(initialized_dir: Path) -> None:
    """Verify task list filters by status and rejects invalid status."""
    runner.invoke(app, ["task", "start", "-t", "T1", "-o", "O1"])
    runner.invoke(app, ["task", "start", "-t", "T2", "-o", "O2"])
    runner.invoke(app, ["task", "complete"])  # completes T2

    # Filter in_progress
    res_prog = runner.invoke(app, ["task", "list", "--status", "in_progress", "--json"])
    assert res_prog.exit_code == 0
    prog_data = json.loads(res_prog.stdout)
    assert len(prog_data) == 1
    assert prog_data[0]["title"] == "T1"

    # Filter completed
    res_comp = runner.invoke(app, ["task", "list", "--status", "completed", "--json"])
    assert res_comp.exit_code == 0
    comp_data = json.loads(res_comp.stdout)
    assert len(comp_data) == 1
    assert comp_data[0]["title"] == "T2"

    # Filter invalid status
    res_invalid = runner.invoke(app, ["task", "list", "--status", "unknown_status"])
    assert res_invalid.exit_code == 2
    assert "Invalid status 'unknown_status'" in res_invalid.stderr


def test_task_show_by_id_json(initialized_dir: Path) -> None:
    """Verify `task show <id> --json` outputs parseable JSON model with all canonical fields."""
    runner.invoke(
        app,
        [
            "task",
            "start",
            "-t",
            "JSON Task",
            "-o",
            "JSON Obj",
            "-r",
            "R1",
            "-c",
            "C1",
        ],
    )

    list_res = runner.invoke(app, ["task", "list", "--json"])
    task_id = json.loads(list_res.stdout)[0]["id"]

    show_res = runner.invoke(app, ["task", "show", task_id, "--json"])
    assert show_res.exit_code == 0
    data = json.loads(show_res.stdout)
    assert data["id"] == task_id
    assert data["title"] == "JSON Task"
    assert data["objective"] == "JSON Obj"
    assert data["requirements"] == ["R1"]
    assert data["constraints"] == ["C1"]
    assert data["status"] == "in_progress"
    assert data["completed_items"] == []
    assert data["completed"] == []
    assert data["remaining_items"] == []
    assert data["remaining"] == []
    assert data["known_issues"] == []
    assert data["is_active"] is True
    assert "created_at" in data
    assert "updated_at" in data


def test_task_update_progress(initialized_dir: Path) -> None:
    """Verify `task update` mutates active task progress lists including aliases."""
    runner.invoke(app, ["task", "start", "-t", "Updatable Task", "-o", "Test updates"])

    upd_res = runner.invoke(
        app,
        [
            "task",
            "update",
            "--work",
            "Writing tests",
            "--add-completed",
            "Step 1",
            "--add-remaining",
            "Step 2",
            "--add-known-issue",
            "Bug 1",
        ],
    )
    assert upd_res.exit_code == 0
    assert "Updated task" in upd_res.stdout

    # Verify through json show
    show_res = runner.invoke(app, ["task", "show", "--json"])
    data = json.loads(show_res.stdout)
    assert data["current_work"] == "Writing tests"
    assert "Step 1" in data["completed_items"]
    assert "Step 1" in data["completed"]
    assert "Step 2" in data["remaining_items"]
    assert "Step 2" in data["remaining"]
    assert "Bug 1" in data["known_issues"]

    # Clear current work
    clear_res = runner.invoke(app, ["task", "update", "--clear-current-work"])
    assert clear_res.exit_code == 0
    show_after_clear = runner.invoke(app, ["task", "show", "--json"])
    assert json.loads(show_after_clear.stdout)["current_work"] is None


def test_task_activate_and_complete(initialized_dir: Path) -> None:
    """Verify switching active tasks and completing them."""
    runner.invoke(app, ["task", "start", "-t", "Task Alpha", "-o", "Obj Alpha"])
    runner.invoke(app, ["task", "start", "-t", "Task Beta", "-o", "Obj Beta"])

    list_data = json.loads(runner.invoke(app, ["task", "list", "--json"]).stdout)
    id_alpha = list_data[0]["id"]
    id_beta = list_data[1]["id"]

    # Beta is active; activate Alpha
    act_res = runner.invoke(app, ["task", "activate", id_alpha])
    assert act_res.exit_code == 0
    assert f"Activated task {id_alpha}" in act_res.stdout

    # Verify Alpha is active
    status_data = json.loads(runner.invoke(app, ["status", "--json"]).stdout)
    assert status_data["active_task"]["id"] == id_alpha

    # Complete Alpha
    comp_res = runner.invoke(app, ["task", "complete"])
    assert comp_res.exit_code == 0
    assert f"Completed task {id_alpha}" in comp_res.stdout

    # Now active task should be None
    status_data_after = json.loads(runner.invoke(app, ["status", "--json"]).stdout)
    assert status_data_after["active_task"] is None

    # Complete Beta by explicit ID
    comp_beta = runner.invoke(app, ["task", "complete", id_beta])
    assert comp_beta.exit_code == 0
    assert f"Completed task {id_beta}" in comp_beta.stdout


def test_task_command_errors(initialized_dir: Path) -> None:
    """Verify expected error messages and non-zero exit codes for invalid operations."""
    # Show nonexistent task
    res1 = runner.invoke(app, ["task", "show", "task_nonexistent"])
    assert res1.exit_code == 1
    assert "was not found" in (res1.stderr + res1.stdout)

    # Complete when no task is active
    res2 = runner.invoke(app, ["task", "complete"])
    assert res2.exit_code == 1
    assert "No active task" in (res2.stderr + res2.stdout)

    # Start and complete a task, then try to activate it
    runner.invoke(app, ["task", "start", "-t", "Done Task", "-o", "Done Obj"])
    list_data = json.loads(runner.invoke(app, ["task", "list", "--json"]).stdout)
    done_id = list_data[0]["id"]
    runner.invoke(app, ["task", "complete"])

    res3 = runner.invoke(app, ["task", "activate", done_id])
    assert res3.exit_code == 1
    assert "terminal status" in (res3.stderr + res3.stdout)


def test_task_in_uninitialized_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify task commands fail gracefully with instructions outside initialized projects."""
    uninit = tmp_path / "empty_dir"
    uninit.mkdir()
    monkeypatch.chdir(uninit)

    res = runner.invoke(app, ["task", "list"])
    assert res.exit_code == 1
    assert "CortexShift is not initialized here." in (res.stderr + res.stdout)
