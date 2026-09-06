"""Integration tests verifying end-to-end CLI execution via subprocess."""

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
    import json

    result = run_cli([sys.executable, "-m", "cortexshift", "doctor", "--json"], env=no_provider_env)
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["cortexshift_version"] == "0.1.0"
    assert "platform" in data
    assert "providers" in data
    assert len(data["providers"]) == 3


def test_python_module_doctor_provider_filter(no_provider_env: dict[str, str]) -> None:
    """Verify `python -m cortexshift doctor --provider claude` filters to Claude."""
    import json

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
