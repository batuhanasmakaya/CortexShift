"""Concrete subprocess command runner adapter."""

import os
import re
import subprocess
from collections.abc import Mapping

from cortexshift.ports.command_runner import CommandResult, CommandRunner

_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
_MAX_OUTPUT_LENGTH = 10_000


def _sanitize_output(text: str | bytes | None) -> str:
    """Sanitize and bound subprocess output string."""
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    cleaned = _ANSI_ESCAPE_RE.sub("", text).strip()
    if len(cleaned) > _MAX_OUTPUT_LENGTH:
        return cleaned[:_MAX_OUTPUT_LENGTH] + "... [truncated]"
    return cleaned


class SubprocessCommandRunner(CommandRunner):
    """Executes commands safely using standard library subprocess.

    Invariants:
    - Never uses shell=True.
    - Commands must be supplied as a list of arguments.
    - Always enforces a finite timeout.
    - Catches OS and process errors without bubbling unhandled exceptions.
    """

    def __init__(self, default_timeout: float = 5.0) -> None:
        self.default_timeout = default_timeout

    def run(
        self,
        command: list[str],
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        if not command:
            return CommandResult(
                command=[],
                exit_code=1,
                stdout="",
                stderr="Empty command provided",
                error_message="Empty command provided",
            )

        eff_timeout = timeout if timeout is not None else self.default_timeout
        eff_env: Mapping[str, str] | None = None
        if env is not None:
            eff_env = {**os.environ, **env}

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=eff_timeout,
                shell=False,
                env=eff_env,
                check=False,
            )
            return CommandResult(
                command=command,
                exit_code=completed.returncode,
                stdout=_sanitize_output(completed.stdout),
                stderr=_sanitize_output(completed.stderr),
            )
        except subprocess.TimeoutExpired:
            return CommandResult(
                command=command,
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {eff_timeout} seconds",
                timed_out=True,
                error_message=f"Command timed out after {eff_timeout} seconds",
            )
        except FileNotFoundError:
            return CommandResult(
                command=command,
                exit_code=127,
                stdout="",
                stderr=f"Executable '{command[0]}' not found",
                not_found=True,
                error_message=f"Executable '{command[0]}' not found",
            )
        except PermissionError:
            return CommandResult(
                command=command,
                exit_code=126,
                stdout="",
                stderr=f"Permission denied executing '{command[0]}'",
                error_message=f"Permission denied executing '{command[0]}'",
            )
        except OSError as exc:
            return CommandResult(
                command=command,
                exit_code=1,
                stdout="",
                stderr=str(exc),
                error_message=str(exc),
            )
