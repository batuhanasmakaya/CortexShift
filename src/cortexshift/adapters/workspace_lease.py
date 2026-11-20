"""File-based OS advisory lock implementation of the WorkspaceLease port."""

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cortexshift.adapters.advisory_lock import is_locked, release_lock, try_lock
from cortexshift.domain.errors import WorkspaceLockedError
from cortexshift.ports.workspace_lease import WorkspaceLease, WorkspaceLeaseManager

STATE_DIR_NAME = ".cortexshift"
LOCK_FILE_NAME = "agent.lock"
PROVIDER_LOCK_FILE_NAME = "provider.lock"


@contextmanager
def hold_provider_guard(project_root: Path) -> Iterator[tuple[int, ...]]:
    """Pass the POSIX guard into an owned process without changing its process group."""
    if sys.platform == "win32":
        yield ()
        return

    path = project_root / STATE_DIR_NAME / PROVIDER_LOCK_FILE_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = try_lock(path, create=True)
    if fd is None:
        raise WorkspaceLockedError(lock_path=path)
    try:
        yield (fd,)
    finally:
        # LOCK_UN would also unlock any descendants' copies of this descriptor.
        os.close(fd)


class FileWorkspaceLease(WorkspaceLease):
    """Workspace lease with a preflight for surviving managed provider work."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = lock_path.parent.resolve() / lock_path.name
        self._provider_lock = self._lock_path.with_name(PROVIDER_LOCK_FILE_NAME)
        self._fd: int | None = None

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def acquire(self) -> bool:
        """Acquire the primary lock before probing the preceding provider's lock."""
        if self._fd is not None:
            return True

        fd = None
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = try_lock(self._lock_path, create=True)
            if fd is None or is_locked(self._provider_lock):
                return False
            self._fd = fd
            return True
        except OSError:
            return False
        finally:
            if fd is not None and self._fd is None:
                release_lock(fd)

    def release(self) -> None:
        """Release the exclusive lock and close the underlying file descriptor."""
        if self._fd is not None:
            fd, self._fd = self._fd, None
            release_lock(fd)

    def is_locked(self) -> bool:
        """Check whether the workspace is unavailable for managed agent operations."""
        return self._fd is not None or is_locked(self._lock_path) or is_locked(self._provider_lock)

    def __enter__(self) -> "FileWorkspaceLease":
        if not self.acquire():
            raise WorkspaceLockedError(lock_path=self._lock_path)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.release()


class FileWorkspaceLeaseManager(WorkspaceLeaseManager):
    """Standard file-backed workspace lease manager."""

    def get_lease(self, project_root: Path) -> WorkspaceLease:
        lock_path = project_root / STATE_DIR_NAME / LOCK_FILE_NAME
        return FileWorkspaceLease(lock_path)
