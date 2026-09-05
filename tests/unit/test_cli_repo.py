"""Unit tests for the `cortexshift repo` CLI command group."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.cli.app import app

runner = CliRunner()


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


def test_repo_help() -> None:
    """Verify `cortexshift repo --help` displays expected subcommands."""
    result = runner.invoke(app, ["repo", "--help"])
    assert result.exit_code == 0
    assert "status" in result.stdout
    assert "snapshot" in result.stdout
    assert "snapshots" in result.stdout
    assert "show" in result.stdout


def test_repo_status_uninitialized_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify error when running repo status in an uninitialized directory."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["repo", "status"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "CortexShift is not initialized here" in output


def test_repo_status_clean_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify live status on a clean repository."""
    _setup_git_repo(tmp_path)
    ProjectInitializationService().initialize(tmp_path, "clean-proj")
    monkeypatch.chdir(tmp_path)

    # Human output
    result = runner.invoke(app, ["repo", "status"])
    assert result.exit_code == 0
    assert "Repository" in result.stdout
    assert "main" in result.stdout
    assert "Clean" in result.stdout
    assert "Dirty" not in result.stdout

    # JSON output
    json_result = runner.invoke(app, ["repo", "status", "--json"])
    assert json_result.exit_code == 0
    data = json.loads(json_result.stdout)
    assert data["status"] == "ready"
    assert data["snapshot"]["branch"] == "main"
    assert data["snapshot"]["dirty"] is False
    assert data["snapshot"]["staged_files"] == []
    assert data["snapshot"]["modified_files"] == []


def test_repo_status_dirty_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify live status reflects modified, staged, and untracked files."""
    _setup_git_repo(tmp_path)
    ProjectInitializationService().initialize(tmp_path, "dirty-proj")
    monkeypatch.chdir(tmp_path)

    # Add changes
    (tmp_path / "app.py").write_text("print('modified')\n")
    (tmp_path / "staged.txt").write_text("staged content")
    subprocess.run(["git", "add", "staged.txt"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "untracked.py").write_text("untracked")

    result = runner.invoke(app, ["repo", "status"])
    assert result.exit_code == 0
    assert "Dirty" in result.stdout
    assert "Staged" in result.stdout
    assert "Modified" in result.stdout
    assert "Untracked" in result.stdout

    json_result = runner.invoke(app, ["repo", "status", "--json"])
    assert json_result.exit_code == 0
    data = json.loads(json_result.stdout)
    assert data["status"] == "ready"
    assert data["snapshot"]["dirty"] is True
    assert "staged.txt" in data["snapshot"]["staged_files"]
    assert "app.py" in data["snapshot"]["modified_files"]
    assert "untracked.py" in data["snapshot"]["untracked_files"]


def test_repo_status_non_git_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify graceful handling of non-Git project (exit code 0, diagnostic message)."""
    ProjectInitializationService().initialize(tmp_path, "nongit-proj")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["repo", "status"])
    assert result.exit_code == 0
    assert "not inside a Git repository" in result.stdout

    json_result = runner.invoke(app, ["repo", "status", "--json"])
    assert json_result.exit_code == 0
    data = json.loads(json_result.stdout)
    assert data["status"] == "not_git_repository"
    assert data["snapshot"] is None


def test_repo_status_git_not_installed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify graceful handling when git executable is not installed (exit code 0)."""
    ProjectInitializationService().initialize(tmp_path, "nogit-proj")
    monkeypatch.chdir(tmp_path)

    with patch("shutil.which", return_value=None):
        result = runner.invoke(app, ["repo", "status"])
        assert result.exit_code == 0
        assert "Git executable was not found in PATH" in result.stdout

        json_result = runner.invoke(app, ["repo", "status", "--json"])
        assert json_result.exit_code == 0
        data = json.loads(json_result.stdout)
        assert data["status"] == "git_not_installed"
        assert data["snapshot"] is None


def test_repo_snapshot_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify capturing and persisting snapshots via CLI."""
    _setup_git_repo(tmp_path)
    ProjectInitializationService().initialize(tmp_path, "snap-proj")
    monkeypatch.chdir(tmp_path)

    # 1. Capture snapshot human output
    result = runner.invoke(app, ["repo", "snapshot"])
    assert result.exit_code == 0
    assert "Repository snapshot captured" in result.stdout
    assert "snap_" in result.stdout

    # 2. Capture snapshot JSON output
    json_result = runner.invoke(app, ["repo", "snapshot", "--json"])
    assert json_result.exit_code == 0
    data = json.loads(json_result.stdout)
    assert data["id"].startswith("snap_")
    assert data["branch"] == "main"


def test_repo_snapshots_list_and_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify listing snapshots and applying --limit."""
    _setup_git_repo(tmp_path)
    ProjectInitializationService().initialize(tmp_path, "list-proj")
    monkeypatch.chdir(tmp_path)

    # Capture 3 snapshots
    for _ in range(3):
        runner.invoke(app, ["repo", "snapshot"])

    # List all
    result = runner.invoke(app, ["repo", "snapshots"])
    assert result.exit_code == 0
    assert "Repository Snapshots" in result.stdout

    # Limit to 1
    limited_result = runner.invoke(app, ["repo", "snapshots", "--limit", "1", "--json"])
    assert limited_result.exit_code == 0
    data = json.loads(limited_result.stdout)
    assert len(data) == 1


def test_repo_show_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify inspecting single snapshot details."""
    _setup_git_repo(tmp_path)
    ProjectInitializationService().initialize(tmp_path, "show-proj")
    monkeypatch.chdir(tmp_path)

    snap_res = runner.invoke(app, ["repo", "snapshot", "--json"])
    snap_data = json.loads(snap_res.stdout)
    snap_id = snap_data["id"]

    # Show valid
    result = runner.invoke(app, ["repo", "show", snap_id])
    assert result.exit_code == 0
    assert snap_id in result.stdout

    # Show JSON
    json_res = runner.invoke(app, ["repo", "show", snap_id, "--json"])
    assert json_res.exit_code == 0
    data = json.loads(json_res.stdout)
    assert data["id"] == snap_id

    # Show nonexistent
    fail_res = runner.invoke(app, ["repo", "show", "snap_nonexistent_12345"])
    assert fail_res.exit_code == 1
    assert "Error:" in (fail_res.stderr + fail_res.stdout) or "not found" in (
        fail_res.stderr + fail_res.stdout
    )


def test_repo_status_from_subdirectory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify running repo status from a nested subdirectory resolves project root."""
    _setup_git_repo(tmp_path)
    ProjectInitializationService().initialize(tmp_path, "subdir-proj")

    child = tmp_path / "src" / "deep"
    child.mkdir(parents=True)
    monkeypatch.chdir(child)

    result = runner.invoke(app, ["repo", "status"])
    assert result.exit_code == 0
    assert "Repository" in result.stdout
    assert "main" in result.stdout
