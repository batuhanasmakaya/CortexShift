"""Local OS advisory locks shared by workspace and invocation lifecycles."""

import errno
import os
import stat
import sys
from pathlib import Path


def try_lock(
    path: Path, *, create: bool = False, exclusive_create: bool = False, shared: bool = False
) -> int | None:
    """Return the locked descriptor, or None only for confirmed lock contention."""
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if exclusive_create:
        flags |= os.O_CREAT | os.O_EXCL
    elif create:
        flags |= os.O_CREAT

    fd = os.open(path, flags, 0o600)
    locked = False
    try:
        opened = os.fstat(fd)
        entry = path.lstat()
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_nlink != 1
            or path.is_junction()
            or not os.path.samestat(opened, entry)
        ):
            raise OSError(errno.EINVAL, "Expected a private regular lock file", path)

        try:
            if sys.platform == "win32":
                import msvcrt

                # Byte-range locks may extend past EOF, so probes never write bytes.
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
                fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except OSError as exc:
            if isinstance(exc, BlockingIOError) or (
                sys.platform == "win32" and exc.errno == errno.EACCES
            ):
                return None
            raise

        locked = True
        return fd
    finally:
        if not locked:
            os.close(fd)


def is_locked(path: Path) -> bool:
    """Probe without creating a file or treating I/O errors as lock contention."""
    try:
        fd = try_lock(path, shared=sys.platform != "win32")
    except FileNotFoundError:
        return False
    if fd is None:
        return True
    release_lock(fd)
    return False


def release_lock(fd: int) -> None:
    """Close the lock promptly, explicitly releasing the Windows byte range."""
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)
