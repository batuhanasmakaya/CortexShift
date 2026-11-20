"""Exercise provider process ownership using disposable native subprocesses."""

import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from cortexshift.adapters.process_runner import SubprocessInteractiveProcessRunner
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.switch_service import SwitchService
from cortexshift.domain.errors import WorkspaceLockedError
from cortexshift.domain.provider import PROVIDER_CODEX
from tests.factories import seed_project, seed_session
from tests.unit.test_switch_service import FakeProcessRunner

POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32", reason="Exercises POSIX descriptor or signal behavior"
)

BACKGROUND = """
import time
from pathlib import Path
Path('working').touch()
deadline = time.monotonic() + 12
while not Path('stop').exists() and time.monotonic() < deadline:
    time.sleep(0.01)
Path('stopped').touch()
"""


def wait_for(predicate: Callable[[], bool]) -> None:
    deadline = time.monotonic() + 8
    while not predicate():
        assert time.monotonic() < deadline, "Timed out waiting for the fake process"
        time.sleep(0.01)


@POSIX_ONLY
def test_switch_waits_for_surviving_provider_work(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The provider exits only after its child announces that it has started.
    code = f"""
import os
if os.fork() == 0:
    exec({BACKGROUND!r})
    os._exit(0)
from pathlib import Path
from time import monotonic, sleep
deadline = monotonic() + 8
while not Path('working').exists():
    assert monotonic() < deadline
    sleep(0.01)
"""
    _, task = seed_project(tmp_path)
    source = seed_session(tmp_path, task.id, provider_id=PROVIDER_CODEX)
    manager = FileWorkspaceLeaseManager()
    first = manager.get_lease(tmp_path)
    second = manager.get_lease(tmp_path)
    provider = SubprocessInteractiveProcessRunner()
    next_provider = FakeProcessRunner()
    switch = SwitchService(
        process_runner=next_provider,
        which_fn=lambda name: name,
        is_tty_fn=lambda: True,
    )
    assert first.acquire()
    try:
        assert provider.run_interactive([sys.executable, "-c", code], tmp_path) == 0
        first.release()
        assert "still holds the workspace guard" in capsys.readouterr().err
        assert (tmp_path / "working").exists()
        assert not (tmp_path / "stopped").exists()
        assert not second.acquire(), "the old provider's child is still active"
        with pytest.raises(WorkspaceLockedError, match="surviving provider work"):
            switch.switch("claude", start_dir=tmp_path)
        assert not next_provider.invocations
        assert not (tmp_path / "stopped").exists()

        (tmp_path / "stop").touch()
        wait_for(lambda: not second.is_locked())
        result = switch.switch("claude", start_dir=tmp_path)
        assert result.handoff.source_session_id == source.id
        assert len(next_provider.invocations) == 1
    finally:
        (tmp_path / "stop").touch()
        second.release()
        first.release()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_direct_exit_releases_workspace_and_reports_uncertainty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], exit_code: int
) -> None:
    runner = SubprocessInteractiveProcessRunner()
    code = f"raise SystemExit({exit_code})"
    lease = FileWorkspaceLeaseManager().get_lease(tmp_path)
    assert lease.acquire()
    try:
        assert runner.run_interactive([sys.executable, "-c", code], tmp_path) == exit_code
    finally:
        lease.release()
    assert not lease.is_locked()
    assert "Activity outside the managed provider process is unknown" in capsys.readouterr().err


@POSIX_ONLY
def test_detached_work_that_closes_fds_and_unrelated_work_are_left_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The provider deliberately starts a process in another session with its FDs closed.
    code = f"""
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep

subprocess.Popen([sys.executable, "-c", {BACKGROUND!r}], start_new_session=True,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
deadline = monotonic() + 8
while not Path('working').exists():
    assert monotonic() < deadline
    sleep(0.01)
"""
    external = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        cwd=tmp_path,
        stdin=subprocess.PIPE,
    )
    lease = FileWorkspaceLeaseManager().get_lease(tmp_path)
    assert lease.acquire()
    try:
        runner = SubprocessInteractiveProcessRunner()
        assert runner.run_interactive([sys.executable, "-c", code], tmp_path) == 0
        lease.release()
        assert "Activity outside the managed provider process is unknown" in capsys.readouterr().err
        assert lease.acquire()
        assert external.poll() is None
        assert not (tmp_path / "stopped").exists()
    finally:
        lease.release()
        (tmp_path / "stop").touch()
        external.communicate(timeout=8)
    wait_for(lambda: (tmp_path / "stopped").exists())


@POSIX_ONLY
@pytest.mark.parametrize("signal_to_send", [signal.SIGINT, signal.SIGTERM])
def test_launcher_signal_preserves_job_control_and_the_guard(
    tmp_path: Path, signal_to_send: signal.Signals
) -> None:
    # Only the test wrapper creates a process group. The real runner keeps the
    # provider in that group, matching how terminal-generated SIGINT is delivered.
    provider_code = (
        "import os, signal; "
        "assert os.getpgrp() == os.getpgid(os.getppid()); "
        "signal.signal(signal.SIGINT, lambda *_: exit(130)); "
    ) + BACKGROUND
    launcher_code = f"""
import sys
from pathlib import Path
from cortexshift.adapters.process_runner import SubprocessInteractiveProcessRunner
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager

lease = FileWorkspaceLeaseManager().get_lease(Path.cwd())
assert lease.acquire()
try:
    result = SubprocessInteractiveProcessRunner().run_interactive(
        [sys.executable, "-c", {provider_code!r}], Path.cwd()
    )
finally:
    lease.release()
raise SystemExit(result)
"""
    launcher = subprocess.Popen(
        [sys.executable, "-c", launcher_code], cwd=tmp_path, start_new_session=True
    )
    lease = FileWorkspaceLeaseManager().get_lease(tmp_path)
    try:
        wait_for(lambda: (tmp_path / "working").exists())
        if signal_to_send == signal.SIGINT:
            os.killpg(launcher.pid, signal.SIGINT)
            assert launcher.wait(timeout=8) == 130
            assert not lease.is_locked()
        else:
            launcher.terminate()
            assert launcher.wait(timeout=8) == -signal.SIGTERM
            assert lease.is_locked()
            assert not lease.acquire()
            assert not (tmp_path / "stopped").exists()
            (tmp_path / "stop").touch()
            wait_for(lambda: not lease.is_locked())
    finally:
        (tmp_path / "stop").touch()
        if launcher.poll() is None:
            launcher.kill()
            launcher.wait(timeout=8)
