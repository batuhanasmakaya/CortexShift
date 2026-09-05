"""File-based OS advisory lock implementation of the WorkspaceLease port."""

import os
import sys
from pathlib import Path

from cortexshift.ports.workspace_lease import WorkspaceLease, WorkspaceLeaseManager

STATE_DIR_NAME = ".cortexshift"
LOCK_FILE_NAME = "agent.lock"


class FileWorkspaceLease(WorkspaceLease):
    """OS-level advisory file lock enforcing single-mutating-agent invariant per project."""

    def __init__(self, lock_path: Path) -> None:
        self._lock_path = Path(lock_path).resolve()
        self._fd: int | None = None

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    def acquire(self) -> bool:
        """Attempt non-blocking exclusive acquisition of the workspace lock."""
        if self._fd is not None:
            return True

        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(str(self._lock_path), os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False

        try:
            if sys.platform == "win32":
                import msvcrt

                if os.path.getsize(str(self._lock_path)) == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            self._fd = fd
            return True
        except (BlockingIOError, OSError):
            os.close(fd)
            return False

    def release(self) -> None:
        """Release the exclusive lock and close the underlying file descriptor."""
        if self._fd is None:
            return

        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            os.close(self._fd)
            self._fd = None

    def is_locked(self) -> bool:
        """Check whether the lock is currently held by any process."""
        if self._fd is not None:
            return True
        if not self._lock_path.exists():
            return False
        try:
            fd = os.open(str(self._lock_path), os.O_RDWR, 0o600)
        except OSError:
            return False

        try:
            if sys.platform == "win32":
                import msvcrt

                if os.path.getsize(str(self._lock_path)) == 0:
                    os.close(fd)
                    return False
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except (BlockingIOError, OSError):
            return True
        finally:
            os.close(fd)

    def __enter__(self) -> "FileWorkspaceLease":
        if not self.acquire():
            from cortexshift.domain.errors import WorkspaceLockedError

            raise WorkspaceLockedError(lock_path=self._lock_path)
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.release()


class FileWorkspaceLeaseManager(WorkspaceLeaseManager):
    """Standard file-backed workspace lease manager."""

    def get_lease(self, project_root: Path) -> WorkspaceLease:
        lock_path = project_root / STATE_DIR_NAME / LOCK_FILE_NAME
        return FileWorkspaceLease(lock_path)
