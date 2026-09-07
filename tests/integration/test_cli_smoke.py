"""Integration tests verifying end-to-end CLI execution via subprocess."""

import json
import os
import sys

import pytest

from tests.cli_runner import path_without_providers, run_cli


@pytest.fixture
def no_provider_env() -> dict[str, str]:
    """Environment for a subprocess that must not find any coding agent.

    `doctor` is the one command that really executes provider CLIs -- version and auth
    probes -- so on a developer machine these tests ran the developer's own `claude` and
    `codex` binaries, and on a runner they ran nothing. Same command, different work.
    Pinning `PATH` makes discovery report the same thing everywhere and keeps the suite
    from invoking a real agent, which it must never do.
    """
    return {**os.environ, "PATH": path_without_providers()}


def test_python_module_help() -> None:
    """Verify `python -m cortexshift --help` runs as a process and displays help."""
    result = run_cli([sys.executable, "-m", "cortexshift", "--help"])
    assert result.returncode == 0
    assert "Switch agents. Keep the context." in result.stdout
    assert "version" in result.stdout


def test_python_module_version() -> None:
    """Verify `python -m cortexshift version` outputs version string."""
    result = run_cli([sys.executable, "-m", "cortexshift", "version"])
    assert result.returncode == 0
    assert "CortexShift 0.1.0" in result.stdout


def test_python_module_version_flag() -> None:
    """Verify `python -m cortexshift --version` outputs version string."""
    result = run_cli([sys.executable, "-m", "cortexshift", "--version"])
    assert result.returncode == 0
    assert "CortexShift 0.1.0" in result.stdout


def test_python_module_doctor(no_provider_env: dict[str, str]) -> None:
    """Verify `python -m cortexshift doctor` runs end-to-end and outputs diagnostic report."""
    result = run_cli([sys.executable, "-m", "cortexshift", "doctor"], env=no_provider_env)
    assert result.returncode == 0
    assert "CortexShift Doctor" in result.stdout
    assert "Environment" in result.stdout
    assert "Providers" in result.stdout


def test_python_module_doctor_json(no_provider_env: dict[str, str]) -> None:
    """Verify `python -m cortexshift doctor --json` outputs parseable JSON."""
    result = run_cli([sys.executable, "-m", "cortexshift", "doctor", "--json"], env=no_provider_env)
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["cortexshift_version"] == "0.1.0"
    assert "platform" in data
    assert "providers" in data
    assert len(data["providers"]) == 3


def test_python_module_doctor_provider_filter(no_provider_env: dict[str, str]) -> None:
    """Verify `python -m cortexshift doctor --provider claude` filters to Claude."""
    result = run_cli(
        [sys.executable, "-m", "cortexshift", "doctor", "--provider", "claude", "--json"],
        env=no_provider_env,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert len(data["providers"]) == 1
    assert data["providers"][0]["provider_id"] == "claude"
    # Nothing was installed for it to find, so no real agent binary was executed.
    assert data["providers"][0]["installed"] is False


def test_python_module_doctor_invalid_provider(no_provider_env: dict[str, str]) -> None:
    """Verify `python -m cortexshift doctor --provider invalid` returns exit code 2."""
    result = run_cli(
        [sys.executable, "-m", "cortexshift", "doctor", "--provider", "invalid_provider"],
        env=no_provider_env,
    )
    assert result.returncode == 2
    assert "Unknown provider" in result.stderr or "Error" in result.stderr


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_reports_survive_a_console_that_cannot_encode_them(
    no_provider_env: dict[str, str], encoding: str
) -> None:
    """A narrow output encoding must degrade a glyph, not kill the command.

    Windows defaults redirected output to the ANSI code page, which has no `✓`, so
    `cortexshift doctor > report.txt` died there with `UnicodeEncodeError` and exit 1
    while the same command on a UTF-8 terminal was fine. `PYTHONIOENCODING` reproduces
    that console on any OS, so this guards the fix everywhere rather than only where the
    bug happened to show up.
    """
    result = run_cli(
        [sys.executable, "-m", "cortexshift", "doctor"],
        env={**no_provider_env, "PYTHONIOENCODING": encoding},
    )

    assert result.returncode == 0, result.stderr
    assert "UnicodeEncodeError" not in result.stderr
    # The report is still the report: only an un-encodable glyph may be degraded.
    assert "CortexShift Doctor" in result.stdout
    assert "Providers" in result.stdout


@pytest.mark.parametrize("encoding", ["cp1252", "ascii"])
def test_machine_readable_output_survives_a_narrow_console(
    no_provider_env: dict[str, str], encoding: str
) -> None:
    """`--json` has to stay parseable whatever the console can encode."""
    result = run_cli(
        [sys.executable, "-m", "cortexshift", "doctor", "--json"],
        env={**no_provider_env, "PYTHONIOENCODING": encoding},
    )

    assert result.returncode == 0, result.stderr
    assert len(json.loads(result.stdout)["providers"]) == 3
