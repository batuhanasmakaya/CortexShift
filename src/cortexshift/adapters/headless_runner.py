"""Subprocess implementation of the HeadlessProviderRunner port."""

import os
import subprocess
from pathlib import Path

from cortexshift.ports.headless_runner import (
    DEFAULT_HEADLESS_TIMEOUT_SECONDS,
    HeadlessProviderRunner,
    HeadlessResult,
)

# Captured provider output is parsed for a handful of machine fields only. The bound
# stops a pathological provider from streaming unbounded output into memory; it is not
# a redaction mechanism, because the output is discarded rather than stored.
_MAX_CAPTURED_OUTPUT_CHARS = 2_000_000


def _bound(text: str | None) -> str:
    """Bound captured output length without altering its leading content."""
    if not text:
        return ""
    if len(text) > _MAX_CAPTURED_OUTPUT_CHARS:
        return text[:_MAX_CAPTURED_OUTPUT_CHARS]
    return text


class SubprocessHeadlessProviderRunner(HeadlessProviderRunner):
    """Runs one non-interactive provider turn, capturing stdout and stderr.

    Invariants:
    - Never uses ``shell=True``; commands are pre-tokenized argument vectors.
    - Requires no TTY and never inherits the user's terminal.
    - Always enforces a finite (but model-turn appropriate) timeout.
    - Never logs or prints captured output; callers parse and discard it.
    """

    def __init__(self, default_timeout: float = DEFAULT_HEADLESS_TIMEOUT_SECONDS) -> None:
        self.default_timeout = default_timeout

    def run_headless(
        self,
        argv: list[str],
        cwd: Path | str,
        timeout: float = DEFAULT_HEADLESS_TIMEOUT_SECONDS,
        env: dict[str, str] | None = None,
    ) -> HeadlessResult:
        """Execute a bounded headless provider process without a shell or TTY."""
        if not argv:
            return HeadlessResult(
                exit_code=1,
                stdout="",
                stderr="Empty command provided",
            )

        full_env = dict(os.environ)
        if env:
            full_env.update(env)

        try:
            completed = subprocess.run(
                argv,
                cwd=str(cwd),
                env=full_env,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
                stdin=subprocess.DEVNULL,
            )
            return HeadlessResult(
                exit_code=completed.returncode,
                stdout=_bound(completed.stdout),
                stderr=_bound(completed.stderr),
            )
        except subprocess.TimeoutExpired:
            return HeadlessResult(
                exit_code=-1,
                stdout="",
                stderr="",
                timed_out=True,
            )
        except (FileNotFoundError, PermissionError):
            return HeadlessResult(
                exit_code=127,
                stdout="",
                stderr="",
                not_found=True,
            )
        except OSError:
            return HeadlessResult(exit_code=1, stdout="", stderr="")
