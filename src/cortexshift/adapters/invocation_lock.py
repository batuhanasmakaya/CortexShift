"""An invocation-specific lock retained only by the CortexShift launcher."""

import contextlib
from collections.abc import Iterator
from pathlib import Path

from cortexshift.adapters.advisory_lock import is_locked, release_lock, try_lock
from cortexshift.adapters.workspace_lease import STATE_DIR_NAME


def _lock_path(root: Path, verifier: str) -> Path:
    return root / STATE_DIR_NAME / f"mcp-{verifier}.lock"


@contextlib.contextmanager
def hold_invocation(root: Path, verifier: str) -> Iterator[None]:
    """Publish a fresh launcher-owned lock. Its descriptor is non-inheritable."""
    path = _lock_path(root, verifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = try_lock(path, exclusive_create=True)
    if fd is None:
        raise RuntimeError("Could not acquire the new MCP invocation lock.")
    try:
        yield
    finally:
        release_lock(fd)
        # A Windows probe may have the file open. An unlocked file grants no authority.
        with contextlib.suppress(OSError):
            path.unlink()


def invocation_is_live(root: Path, verifier: str) -> bool:
    """Check under the SQLite write reservation so Windows probes cannot overlap."""
    try:
        return is_locked(_lock_path(root, verifier))
    except OSError:
        return False
