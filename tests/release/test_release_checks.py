"""Release guards must reject drift and accidental private artifacts."""

import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from scripts.release_check import validate_names, validate_tag

from tests.cli_runner import run_cli

ROOT = Path(__file__).resolve().parents[2]


def test_single_version_source() -> None:
    import cortexshift

    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert cortexshift.__version__ == importlib.metadata.version("cortexshift") == version
    for args in [["--version"], ["version"]]:
        result = run_cli([sys.executable, "-m", "cortexshift", *args])
        assert result.returncode == 0
        assert result.stdout.strip() == f"CortexShift {version}"


@pytest.mark.parametrize("tag", ["0.1.0", "v0.2.0", "v0.1.0rc1", "v0.1.0\n"])
def test_bad_release_tags_rejected(tag: str) -> None:
    with pytest.raises(ValueError):
        validate_tag(tag, "0.1.0")


def test_matching_release_tag() -> None:
    validate_tag("v0.1.0", "0.1.0")


@pytest.mark.parametrize(
    "name",
    [
        ".cortexshift/agent.lock",
        "x/.venv/a",
        "x/a.pyc",
        "../escape",
        "/absolute",
        "x/state.sqlite3",
    ],
)
def test_private_archive_entries_rejected(name: str) -> None:
    with pytest.raises(ValueError):
        validate_names([name])


def test_package_import_has_no_runtime_initialization(tmp_path: Path) -> None:
    code = """
import sys
import cortexshift
for name in ['sqlite3', 'textual', 'mcp', 'cortexshift.application']:
    assert name not in sys.modules, name
"""
    subprocess.run([sys.executable, "-c", code], cwd=tmp_path, check=True)
