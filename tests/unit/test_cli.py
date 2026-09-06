"""Tests for CLI commands and behavior."""

from cortexshift import __version__
from cortexshift.cli.app import app
from tests.cli_runner import AnsiFreeCliRunner

runner = AnsiFreeCliRunner()


def test_cli_help() -> None:
    """Verify `cortexshift --help` outputs help and exits cleanly."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Switch agents. Keep the context." in result.stdout
    assert "version" in result.stdout


def test_cli_no_args_shows_help() -> None:
    """Verify running `cortexshift` without arguments displays help."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "Switch agents. Keep the context." in result.stdout


def test_cli_version_command() -> None:
    """Verify `cortexshift version` outputs the correct version."""
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"CortexShift {__version__}" in result.stdout


def test_cli_version_flag() -> None:
    """Verify `cortexshift --version` and `-v` output the correct version."""
    result_long = runner.invoke(app, ["--version"])
    assert result_long.exit_code == 0
    assert f"CortexShift {__version__}" in result_long.stdout

    result_short = runner.invoke(app, ["-v"])
    assert result_short.exit_code == 0
    assert f"CortexShift {__version__}" in result_short.stdout


def test_cli_unknown_command_fails() -> None:
    """Verify unknown command returns non-zero status."""
    result = runner.invoke(app, ["nonexistent-cmd"])
    assert result.exit_code != 0
