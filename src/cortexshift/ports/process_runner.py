"""Port defining the interface for running interactive provider processes."""

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class InteractiveProcessRunner(Protocol):
    """Abstract port for running long-lived interactive provider processes.

    Unlike diagnostic command runners, interactive runners inherit terminal
    streams (stdin, stdout, stderr) and do not impose short timeouts.
    """

    def run_interactive(
        self,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        """Run a process interactively inheriting the terminal.

        Args:
            argv: Argument vector to execute without a shell.
            cwd: Canonical working directory for execution.
            env: Optional environment dictionary overlay.

        Returns:
            The exit code of the terminated process.
        """
        ...
