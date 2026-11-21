"""Subprocess implementation of the InteractiveProcessRunner port."""

import os
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from typing import BinaryIO

from cortexshift.adapters.advisory_lock import is_locked
from cortexshift.adapters.workspace_lease import (
    PROVIDER_LOCK_FILE_NAME,
    STATE_DIR_NAME,
    hold_provider_guard,
)
from cortexshift.ports.process_runner import InteractiveProcessRunner


class SubprocessInteractiveProcessRunner(InteractiveProcessRunner):
    """Executes native provider processes interactively inheriting terminal streams."""

    def __init__(self, terminal_stdin: BinaryIO | None = None) -> None:
        self._terminal_stdin = terminal_stdin

    def run_interactive(
        self,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        """Run a native provider process interactively without short timeouts.

        Args:
            argv: Argument vector to execute directly (never through a shell).
            cwd: Working directory (canonical project root).
            env: Optional environment overlay.

        Returns:
            The exit code of the process (e.g. 0 on success, 130 on SIGINT).
        """
        full_env = dict(os.environ)
        if env:
            full_env.update(env)

        # Keep native terminal streams and job control. On POSIX, a descendant
        # that retains this descriptor keeps the lock even after reparenting.
        proc = None
        try:
            with hold_provider_guard(Path(cwd)) as pass_fds:
                try:
                    proc = subprocess.Popen(
                        argv,
                        cwd=str(cwd),
                        env=full_env,
                        stdin=self._terminal_stdin,
                        stdout=None,
                        stderr=None,
                        shell=False,
                        pass_fds=pass_fds,
                    )
                    return proc.wait()
                except KeyboardInterrupt:
                    if proc is not None:
                        return self._wait_after_interrupt(proc)
                    return 130
        finally:
            if proc is not None:
                self._report_remaining_activity(Path(cwd))

    @staticmethod
    def _wait_after_interrupt(proc: subprocess.Popen[bytes]) -> int:
        try:
            return proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                return proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                return proc.wait()

    @staticmethod
    def _report_remaining_activity(root: Path) -> None:
        retained = False
        if sys.platform != "win32":
            with suppress(OSError):
                retained = is_locked(root / STATE_DIR_NAME / PROVIDER_LOCK_FILE_NAME)

        if retained:
            sys.stderr.write(
                "CortexShift: Provider-associated work still holds the workspace guard. "
                "Wait for it to finish or stop it using its normal controls before retrying.\n"
            )
        else:
            sys.stderr.write(
                "CortexShift: Activity outside the managed provider process is unknown. "
                "Check background jobs before handing off the workspace.\n"
            )
