"""Unit tests for workspace lease exclusivity and concurrency safety."""

from pathlib import Path

import pytest

from cortexshift.adapters.workspace_lease import (
    LOCK_FILE_NAME,
    FileWorkspaceLease,
    FileWorkspaceLeaseManager,
)
from cortexshift.domain.errors import WorkspaceLockedError


def test_first_lease_acquisition_succeeds(tmp_path: Path) -> None:
    """Verify acquiring an unheld lease succeeds and creates the lock file."""
    lock_file = tmp_path / ".cortexshift" / LOCK_FILE_NAME
    lease = FileWorkspaceLease(lock_file)

    assert lease.is_locked() is False
    assert lease.acquire() is True
    assert lease.is_locked() is True
    assert lock_file.is_file()

    lease.release()
    assert lease.is_locked() is False


def test_second_concurrent_lease_fails(tmp_path: Path) -> None:
    """Verify that a second concurrent lease on the same workspace is rejected."""
    lock_file = tmp_path / ".cortexshift" / LOCK_FILE_NAME
    lease1 = FileWorkspaceLease(lock_file)
    lease2 = FileWorkspaceLease(lock_file)

    assert lease1.acquire() is True
    assert lease2.acquire() is False

    # After lease1 releases, lease2 should be able to acquire
    lease1.release()
    assert lease2.acquire() is True
    lease2.release()


def test_context_manager_usage(tmp_path: Path) -> None:
    """Verify workspace lease works as a context manager."""
    lock_file = tmp_path / ".cortexshift" / LOCK_FILE_NAME
    lease1 = FileWorkspaceLease(lock_file)

    with lease1:
        assert lease1.is_locked() is True
        lease2 = FileWorkspaceLease(lock_file)
        with pytest.raises(WorkspaceLockedError) as exc_info, lease2:
            pass
        assert "Another CortexShift agent session is already active" in str(exc_info.value)

    assert lease1.is_locked() is False


def test_independent_projects_do_not_block_each_other(tmp_path: Path) -> None:
    """Verify that leases on different project workspaces are completely independent."""
    proj_a = tmp_path / "project_a" / ".cortexshift" / LOCK_FILE_NAME
    proj_b = tmp_path / "project_b" / ".cortexshift" / LOCK_FILE_NAME

    lease_a = FileWorkspaceLease(proj_a)
    lease_b = FileWorkspaceLease(proj_b)

    assert lease_a.acquire() is True
    assert lease_b.acquire() is True

    assert lease_a.is_locked() is True
    assert lease_b.is_locked() is True

    lease_a.release()
    assert lease_a.is_locked() is False
    assert lease_b.is_locked() is True

    lease_b.release()
    assert lease_b.is_locked() is False


def test_lease_manager_factory(tmp_path: Path) -> None:
    """Verify FileWorkspaceLeaseManager derives standard lock file path under .cortexshift/."""
    manager = FileWorkspaceLeaseManager()
    lease = manager.get_lease(tmp_path)

    expected_path = tmp_path / ".cortexshift" / LOCK_FILE_NAME
    assert lease.lock_path == expected_path.resolve()


def test_unlocked_existing_file_can_be_acquired(tmp_path: Path) -> None:
    """Verify that an existing lock file on disk can be acquired if no process holds an fd lock."""
    lock_file = tmp_path / ".cortexshift" / LOCK_FILE_NAME
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text("prior_run_data")

    lease = FileWorkspaceLease(lock_file)
    assert lease.is_locked() is False
    assert lease.acquire() is True
    assert lease.is_locked() is True

    lease.release()
    assert lease.is_locked() is False
    assert lock_file.exists()


def test_cross_process_lock_concurrency(tmp_path: Path) -> None:
    """Verify cross-process lock contention using real OS file descriptors."""
    import subprocess
    import sys

    lock_file = tmp_path / ".cortexshift" / LOCK_FILE_NAME

    script = (
        "import sys\n"
        "from pathlib import Path\n"
        "from cortexshift.adapters.workspace_lease import FileWorkspaceLease\n"
        f"lease = FileWorkspaceLease(Path({str(lock_file)!r}))\n"
        "if lease.acquire():\n"
        "    sys.stdout.write('ACQUIRED\\n')\n"
        "    sys.stdout.flush()\n"
        "    sys.stdin.readline()\n"
        "    lease.release()\n"
        "    sys.stdout.write('RELEASED\\n')\n"
        "    sys.stdout.flush()\n"
        "else:\n"
        "    sys.stdout.write('FAILED\\n')\n"
        "    sys.stdout.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        assert line == "ACQUIRED"

        # Contending lease in this process must fail
        local_lease = FileWorkspaceLease(lock_file)
        assert local_lease.is_locked() is True
        assert local_lease.acquire() is False

        # Signal helper process to release and exit
        assert proc.stdin is not None
        proc.stdin.write("\n")
        proc.stdin.flush()
        proc.wait(timeout=5)

        # Once released, acquisition must succeed
        assert local_lease.acquire() is True
        assert local_lease.is_locked() is True
        local_lease.release()
        assert local_lease.is_locked() is False
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
