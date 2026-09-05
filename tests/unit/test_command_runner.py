"""Unit tests for CommandRunner abstraction and SubprocessCommandRunner."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from cortexshift.adapters.command_runner import SubprocessCommandRunner, _sanitize_output
from cortexshift.ports.command_runner import CommandResult


def test_command_result_success_property() -> None:
    res = CommandResult(
        command=["echo", "hi"],
        exit_code=0,
        stdout="hi",
        stderr="",
    )
    assert res.success is True

    res_fail = CommandResult(
        command=["echo", "hi"],
        exit_code=1,
        stdout="",
        stderr="err",
    )
    assert res_fail.success is False

    res_timeout = CommandResult(
        command=["echo", "hi"],
        exit_code=0,
        stdout="",
        stderr="",
        timed_out=True,
    )
    assert res_timeout.success is False

    res_not_found = CommandResult(
        command=["echo", "hi"],
        exit_code=0,
        stdout="",
        stderr="",
        not_found=True,
    )
    assert res_not_found.success is False


def test_sanitize_output() -> None:
    assert _sanitize_output(None) == ""
    assert _sanitize_output(b"hello world\n") == "hello world"
    # ANSI escape codes stripped
    assert _sanitize_output("\x1b[32mSuccess\x1b[0m\n") == "Success"
    # Length truncation
    long_text = "x" * 12_000
    sanitized = _sanitize_output(long_text)
    assert len(sanitized) < 12_000
    assert sanitized.endswith("... [truncated]")


def test_subprocess_command_runner_empty_command() -> None:
    runner = SubprocessCommandRunner()
    result = runner.run([])
    assert result.exit_code == 1
    assert result.success is False
    assert "Empty command" in result.stderr


def test_subprocess_command_runner_real_command() -> None:
    runner = SubprocessCommandRunner()
    # Safe cross-platform python command
    import sys

    result = runner.run([sys.executable, "-c", "print('hello')"])
    assert result.exit_code == 0
    assert result.success is True
    assert result.stdout == "hello"


def test_subprocess_command_runner_nonzero_exit() -> None:
    runner = SubprocessCommandRunner()
    import sys

    result = runner.run([sys.executable, "-c", "import sys; sys.exit(42)"])
    assert result.exit_code == 42
    assert result.success is False


def test_subprocess_command_runner_timeout() -> None:
    runner = SubprocessCommandRunner(default_timeout=0.2)
    import sys

    result = runner.run([sys.executable, "-c", "import time; time.sleep(1.0)"])
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.success is False
    assert "timed out" in result.stderr


def test_subprocess_command_runner_file_not_found() -> None:
    runner = SubprocessCommandRunner()
    result = runner.run(["non_existent_binary_xyz_12345"])
    assert result.not_found is True
    assert result.exit_code == 127
    assert result.success is False
    assert "not found" in result.stderr


def test_subprocess_command_runner_permission_error() -> None:
    runner = SubprocessCommandRunner()
    with patch("subprocess.run", side_effect=PermissionError("Permission denied")):
        result = runner.run(["dummy_cmd"])
        assert result.exit_code == 126
        assert result.success is False
        assert "Permission denied" in result.stderr


def test_subprocess_command_runner_os_error() -> None:
    runner = SubprocessCommandRunner()
    with patch("subprocess.run", side_effect=OSError("OS failure")):
        result = runner.run(["dummy_cmd"])
        assert result.exit_code == 1
        assert result.success is False
        assert "OS failure" in result.stderr


def test_subprocess_command_runner_with_custom_env() -> None:
    runner = SubprocessCommandRunner()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="val", stderr="")
        result = runner.run(["dummy"], env={"MY_TEST_VAR": "val"})
        assert result.success is True
        mock_run.assert_called_once()
        called_env = mock_run.call_args[1]["env"]
        assert called_env["MY_TEST_VAR"] == "val"


def test_subprocess_command_runner_with_cwd(tmp_path: Path) -> None:
    runner = SubprocessCommandRunner()
    import sys

    script = "import os; print(os.getcwd())"
    result = runner.run([sys.executable, "-c", script], cwd=tmp_path)
    assert result.success is True
    assert Path(result.stdout).resolve() == tmp_path.resolve()


def test_subprocess_command_runner_sanitize_false() -> None:
    runner = SubprocessCommandRunner()
    import sys

    # Print raw NUL byte and spaces
    script = "import sys; sys.stdout.write('  hello\\x00world  ')"
    result = runner.run([sys.executable, "-c", script], sanitize=False)
    assert result.success is True
    assert result.stdout == "  hello\x00world  "
