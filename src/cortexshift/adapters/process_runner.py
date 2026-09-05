"""Subprocess implementation of the InteractiveProcessRunner port."""

import os
import subprocess
from pathlib import Path

from cortexshift.ports.process_runner import InteractiveProcessRunner


class SubprocessInteractiveProcessRunner(InteractiveProcessRunner):
    """Executes native provider processes interactively inheriting terminal streams."""

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

        proc = None
        try:
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=full_env,
                stdin=None,
                stdout=None,
                stderr=None,
                shell=False,
            )
            return proc.wait()
        except KeyboardInterrupt:
            if proc is not None:
                try:
                    return proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    try:
                        return proc.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        return proc.wait()
            return 130
