"""Integration tests verifying end-to-end CLI execution via subprocess."""

import subprocess
import sys


def test_python_module_help() -> None:
    """Verify `python -m cortexshift --help` runs as a process and displays help."""
    result = subprocess.run(
        [sys.executable, "-m", "cortexshift", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "Switch agents. Keep the context." in result.stdout
    assert "version" in result.stdout


def test_python_module_version() -> None:
    """Verify `python -m cortexshift version` outputs version string."""
    result = subprocess.run(
        [sys.executable, "-m", "cortexshift", "version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "CortexShift 0.1.0" in result.stdout


def test_python_module_version_flag() -> None:
    """Verify `python -m cortexshift --version` outputs version string."""
    result = subprocess.run(
        [sys.executable, "-m", "cortexshift", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "CortexShift 0.1.0" in result.stdout


def test_python_module_doctor() -> None:
    """Verify `python -m cortexshift doctor` runs end-to-end and outputs diagnostic report."""
    result = subprocess.run(
        [sys.executable, "-m", "cortexshift", "doctor"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "CortexShift Doctor" in result.stdout
    assert "Environment" in result.stdout
    assert "Providers" in result.stdout


def test_python_module_doctor_json() -> None:
    """Verify `python -m cortexshift doctor --json` outputs parseable JSON."""
    import json

    result = subprocess.run(
        [sys.executable, "-m", "cortexshift", "doctor", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["cortexshift_version"] == "0.1.0"
    assert "platform" in data
    assert "providers" in data
    assert len(data["providers"]) == 3


def test_python_module_doctor_provider_filter() -> None:
    """Verify `python -m cortexshift doctor --provider claude` filters to Claude."""
    import json

    result = subprocess.run(
        [sys.executable, "-m", "cortexshift", "doctor", "--provider", "claude", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert len(data["providers"]) == 1
    assert data["providers"][0]["provider_id"] == "claude"


def test_python_module_doctor_invalid_provider() -> None:
    """Verify `python -m cortexshift doctor --provider invalid` returns exit code 2."""
    result = subprocess.run(
        [sys.executable, "-m", "cortexshift", "doctor", "--provider", "invalid_provider"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "Unknown provider" in result.stderr or "Error" in result.stderr
