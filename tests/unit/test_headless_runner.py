"""Unit tests for the bounded headless provider runner adapter."""

import sys
from pathlib import Path

from cortexshift.adapters.headless_runner import SubprocessHeadlessProviderRunner
from cortexshift.ports.headless_runner import (
    DEFAULT_HEADLESS_TIMEOUT_SECONDS,
    HeadlessProviderRunner,
)


def test_adapter_satisfies_port() -> None:
    """Verify the subprocess adapter structurally implements HeadlessProviderRunner."""
    assert isinstance(SubprocessHeadlessProviderRunner(), HeadlessProviderRunner)


def test_default_timeout_allows_a_full_model_turn() -> None:
    """Verify the default bound is generous enough for one planning turn but finite."""
    assert 60.0 <= DEFAULT_HEADLESS_TIMEOUT_SECONDS <= 900.0


def test_captures_stdout_and_exit_code(tmp_path: Path) -> None:
    """Verify stdout, stderr, and exit code are captured without a shell."""
    result = SubprocessHeadlessProviderRunner().run_headless(
        argv=[
            sys.executable,
            "-c",
            'import sys; print(\'{"conversation_id": "c1"}\'); '
            "print('warn', file=sys.stderr); sys.exit(0)",
        ],
        cwd=tmp_path,
    )

    assert result.exit_code == 0
    assert "conversation_id" in result.stdout
    assert "warn" in result.stderr
    assert result.timed_out is False
    assert result.not_found is False


def test_nonzero_exit_is_reported_not_raised(tmp_path: Path) -> None:
    """Verify process failure surfaces as a result rather than an exception."""
    result = SubprocessHeadlessProviderRunner().run_headless(
        argv=[sys.executable, "-c", "import sys; sys.exit(3)"],
        cwd=tmp_path,
    )
    assert result.exit_code == 3


def test_missing_executable_is_reported(tmp_path: Path) -> None:
    """Verify a missing binary is classified rather than raising."""
    result = SubprocessHeadlessProviderRunner().run_headless(
        argv=[str(tmp_path / "definitely-not-a-real-binary")],
        cwd=tmp_path,
    )
    assert result.not_found is True
    assert result.exit_code == 127


def test_timeout_is_enforced(tmp_path: Path) -> None:
    """Verify a hung provider cannot wedge a switch indefinitely."""
    result = SubprocessHeadlessProviderRunner().run_headless(
        argv=[sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        timeout=0.5,
    )
    assert result.timed_out is True
    assert result.stdout == ""


def test_arguments_are_not_shell_interpreted(tmp_path: Path) -> None:
    """Verify shell metacharacters in arguments are passed through inertly."""
    marker = tmp_path / "hacked"
    result = SubprocessHeadlessProviderRunner().run_headless(
        argv=[
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1])",
            f"$(touch {marker}); rm -rf / && echo pwned",
        ],
        cwd=tmp_path,
    )

    assert result.exit_code == 0
    assert "$(touch" in result.stdout
    assert not marker.exists()


def test_empty_argv_is_rejected_safely(tmp_path: Path) -> None:
    """Verify an empty command never reaches the operating system."""
    result = SubprocessHeadlessProviderRunner().run_headless(argv=[], cwd=tmp_path)
    assert result.exit_code == 1
    assert result.stdout == ""


def test_runs_in_the_given_working_directory(tmp_path: Path) -> None:
    """Verify the provider process runs in the canonical project root."""
    result = SubprocessHeadlessProviderRunner().run_headless(
        argv=[sys.executable, "-c", "import os; print(os.getcwd())"],
        cwd=tmp_path,
    )
    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()
