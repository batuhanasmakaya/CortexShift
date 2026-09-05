"""Port defining exclusive workspace leasing to enforce single mutating agent invariant."""

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class WorkspaceLease(Protocol):
    """An acquired or acquirable exclusive lease on a CortexShift workspace."""

    @property
    def lock_path(self) -> Path:
        """The absolute path to the lock file."""
        ...

    def acquire(self) -> bool:
        """Attempt to acquire the exclusive non-blocking lease.

        Returns:
            True if the lease was acquired successfully, False if already held.
        """
        ...

    def release(self) -> None:
        """Release the held exclusive lease."""
        ...

    def is_locked(self) -> bool:
        """Check whether the workspace is currently locked by any process."""
        ...


@runtime_checkable
class WorkspaceLeaseManager(Protocol):
    """Factory port for creating workspace lease instances for a project."""

    def get_lease(self, project_root: Path) -> WorkspaceLease:
        """Return a WorkspaceLease configured for the given project root."""
        ...
