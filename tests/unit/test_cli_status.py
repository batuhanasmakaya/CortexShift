"""Unit tests for `cortexshift status` CLI command."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.cli.app import app
from cortexshift.domain.task import Task

runner = CliRunner()


def test_status_uninitialized_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify status outside initialized project reports instructions and exits with 1."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "CortexShift is not initialized here." in output
    assert "cortexshift init" in output


def test_status_rich_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify rich human-readable status output."""
    monkeypatch.chdir(tmp_path)

    # Initialize
    runner.invoke(app, ["init", "--name", "Saturn"])

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "CortexShift Status" in result.stdout
    assert "Saturn" in result.stdout
    assert "State" in result.stdout
    assert "Active Task" in result.stdout


def test_status_json_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `cortexshift status --json` outputs parseable canonical JSON."""
    monkeypatch.chdir(tmp_path)

    runner.invoke(app, ["init", "--name", "Jupiter"])

    # Create and activate a task directly in the DB
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        proj = store.get_default_project()
        assert proj is not None
        task = Task(
            project_id=proj.id,
            title="Orbit Jupiter",
            objective="Deploy probes",
            completed_items=["Stage 1"],
            remaining_items=["Stage 2", "Stage 3"],
            known_issues=["Radiation"],
        )
        store.save_task(task)
        store.set_active_task_id(proj.id, task.id)

    result = runner.invoke(app, ["status", "--json"])
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data["name"] == "Jupiter"
    assert data["schema_version"] == 3
    assert data["state_file"] == ".cortexshift/state.sqlite3"

    assert data["active_task"] is not None
    assert data["active_task"]["id"] == task.id
    assert data["active_task"]["title"] == "Orbit Jupiter"
    assert data["active_task"]["progress"]["completed"] == 1
    assert data["active_task"]["progress"]["remaining"] == 2
    assert data["active_task"]["progress"]["issues"] == 1


def test_status_from_nested_subdirectory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify status finds ancestor state when executed from a nested subdirectory."""
    runner.invoke(app, ["init", str(tmp_path), "--name", "RootProject"])

    nested = tmp_path / "src" / "deep" / "nested"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "RootProject" in result.stdout
