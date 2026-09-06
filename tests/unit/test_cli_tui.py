"""`cortexshift tui` entrypoint behaviour.

The command exists, requires an initialized project, requires a real terminal, and does
not change what plain `cortexshift` does.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.cli.app import app

runner = CliRunner()


def test_tui_command_is_registered() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "tui" in result.stdout


def test_tui_help_describes_the_terminal_handoff() -> None:
    result = runner.invoke(app, ["tui", "--help"])
    assert result.exit_code == 0
    assert "dashboard" in result.stdout.lower()


def test_plain_cortexshift_still_shows_help_and_never_opens_the_dashboard() -> None:
    """Phase 9 does not change the default CLI behaviour."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Usage" in result.stdout
    assert "Switch agents" in result.stdout


def test_tui_refuses_to_open_outside_an_initialized_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["tui"])

    assert result.exit_code == 1
    output = result.stdout + (result.stderr or "")
    assert "CortexShift is not initialized here." in output
    assert "cortexshift init" in output


def test_tui_requires_an_interactive_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-TTY invocation fails cleanly instead of opening a broken dashboard."""
    ProjectInitializationService().initialize(tmp_path, name="Headless")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["tui"])

    assert result.exit_code == 1
    output = result.stdout + (result.stderr or "")
    assert "interactive terminal" in output
    assert "Traceback" not in output


def test_existing_commands_remain_registered() -> None:
    """Phase 9 adds a command; it removes none."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in (
        "version",
        "doctor",
        "init",
        "status",
        "task",
        "repo",
        "run",
        "resume",
        "session",
        "handoff",
        "switch",
        "checkpoint",
        "recover",
        "mcp",
        "tui",
    ):
        assert command in result.stdout, f"{command} disappeared from the CLI"
