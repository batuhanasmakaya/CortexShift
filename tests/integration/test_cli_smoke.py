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
