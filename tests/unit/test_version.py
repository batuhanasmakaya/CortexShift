"""Tests for package version."""

import re

import cortexshift


def test_version() -> None:
    """Verify package version is set to 0.1.0."""
    assert cortexshift.__version__ == "0.1.0"
    assert re.match(r"^\d+\.\d+\.\d+", cortexshift.__version__) is not None
