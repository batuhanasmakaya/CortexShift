"""Unit tests for `cortexshift init` CLI command."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cortexshift.cli.app import app

runner = CliRunner()


def test_cli_init_fresh_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `cortexshift init` creates state and prints summary."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "Initialized CortexShift" in result.stdout
    assert "Project" in result.stdout
    assert (tmp_path / ".cortexshift" / "state.sqlite3").is_file()


def test_cli_init_with_custom_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `cortexshift init --name` sets custom project name."""
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--name", "Falcon"])
    assert result.exit_code == 0
    assert "Initialized CortexShift" in result.stdout
    assert "Falcon" in result.stdout


def test_cli_init_with_explicit_path(tmp_path: Path) -> None:
    """Verify `cortexshift init <path>` initializes specified directory."""
    target = tmp_path / "subproject"
    target.mkdir()

    result = runner.invoke(app, ["init", str(target), "--name", "SubProj"])
    assert result.exit_code == 0
    assert (target / ".cortexshift" / "state.sqlite3").is_file()


def test_cli_init_idempotent_reinvocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify running `cortexshift init` twice reports already initialized with exit code 0."""
    monkeypatch.chdir(tmp_path)

    res1 = runner.invoke(app, ["init", "--name", "Orion"])
    assert res1.exit_code == 0
    assert "Initialized CortexShift" in res1.stdout

    res2 = runner.invoke(app, ["init"])
    assert res2.exit_code == 0
    assert "CortexShift is already initialized for Orion" in res2.stdout


def test_cli_init_help() -> None:
    """Verify `cortexshift init --help` displays usage documentation."""
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0
    assert "Initialize a project-local CortexShift workspace." in result.stdout
