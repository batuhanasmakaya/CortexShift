"""Port defining the interface for safe external command execution."""

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class CommandResult(BaseModel):
    """Result of an executed external command."""

    model_config = ConfigDict(frozen=True)

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    not_found: bool = False
    error_message: str | None = None

    @property
    def success(self) -> bool:
        """Whether the command exited with returncode 0."""
        return self.exit_code == 0 and not self.timed_out and not self.not_found


@runtime_checkable
class CommandRunner(Protocol):
    """Abstract port for executing native CLI commands safely.

    Implementations must avoid shell=True, execute commands as argument lists,
    enforce finite timeouts, and handle process failures gracefully.
    """

    def run(
        self,
        command: list[str],
        timeout: float = 5.0,
        env: dict[str, str] | None = None,
        cwd: Path | str | None = None,
        sanitize: bool = True,
    ) -> CommandResult:
        """Execute a command as an argument list with a finite timeout.

        Args:
            command: Command and arguments as a list of strings.
            timeout: Maximum execution duration in seconds.
            env: Optional environment dictionary override.
            cwd: Optional working directory for command execution.
            sanitize: Whether to sanitize, strip ANSI escapes, and bound stdout/stderr.

        Returns:
            CommandResult containing exit code, stdout, stderr, and failure flags.
        """
        ...
