"""Port defining bounded headless execution of a single provider model turn.

Distinct from the short-timeout diagnostic ``CommandRunner`` used by provider discovery:
a headless provider bootstrap is one full model turn and legitimately needs minutes,
while still being strictly non-interactive, shell-free, and silent by default.
"""

from pathlib import Path
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

# A single provider planning turn may legitimately take minutes. The bound stays finite
# so a hung provider can never wedge a CortexShift switch indefinitely.
DEFAULT_HEADLESS_TIMEOUT_SECONDS = 300.0


class HeadlessResult(BaseModel):
    """Result of a bounded headless provider invocation.

    Captured output is returned to the caller for minimal machine-field parsing only.
    Adapters must discard it after extracting required metadata and must never persist
    or log it.
    """

    model_config = ConfigDict(frozen=True)

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    not_found: bool = False


@runtime_checkable
class HeadlessProviderRunner(Protocol):
    """Abstract port for running one non-interactive provider turn without a TTY."""

    def run_headless(
        self,
        argv: list[str],
        cwd: Path | str,
        timeout: float = DEFAULT_HEADLESS_TIMEOUT_SECONDS,
        env: dict[str, str] | None = None,
    ) -> HeadlessResult:
        """Execute a provider process headlessly, capturing stdout and stderr.

        Args:
            argv: Argument vector executed directly, never through a shell.
            cwd: Canonical working directory (the project root).
            timeout: Finite upper bound appropriate for a single model turn.
            env: Optional environment overlay.

        Returns:
            A HeadlessResult describing the outcome. Implementations must not raise
            on process failure, timeout, or a missing executable.
        """
        ...
