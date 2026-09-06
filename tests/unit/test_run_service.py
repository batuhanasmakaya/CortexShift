"""Unit tests for RunService application orchestrator."""

from pathlib import Path
from typing import Any

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.run_service import RunService
from cortexshift.domain.errors import (
    NoActiveTaskError,
    ProjectNotInitializedError,
    ProviderNotFoundError,
    TerminalRequiredError,
    UnknownProviderError,
    UnsupportedPromptError,
    WorkspaceLockedError,
)
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.ports.process_runner import InteractiveProcessRunner


class FakeProcessRunner(InteractiveProcessRunner):
    """Test fake for InteractiveProcessRunner recording invocation parameters."""

    def __init__(self, exit_code: int = 0, raise_interrupt: bool = False) -> None:
        self.exit_code = exit_code
        self.raise_interrupt = raise_interrupt
        self.invocations: list[dict[str, Any]] = []

    def run_interactive(
        self,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        self.invocations.append({"argv": argv, "cwd": cwd, "env": env})
        if self.raise_interrupt:
            raise KeyboardInterrupt()
        return self.exit_code


def _setup_test_project(
    root_path: Path, with_active_task: bool = True
) -> tuple[Project, Task | None]:
    """Helper to initialize a project with an active task in root_path."""
    db_file = root_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name="RunServiceTest", repo_path=str(root_path))
        store.save_project(project)

        task = None
        if with_active_task:
            task = Task(
                project_id=project.id,
                title="Run Task",
                objective="Test provider launch",
            )
            store.save_task(task)
            store.set_active_task_id(project.id, task.id)

    return project, task


def test_dry_run_success(tmp_path: Path) -> None:
    """Verify dry run builds specification without spawning child process or session."""
    _setup_test_project(tmp_path, with_active_task=True)

    runner = FakeProcessRunner()
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: "/bin/claude",
        is_tty_fn=lambda: True,
    )

    result = service.dry_run(
        provider_name="claude",
        prompt="Sensitive prompt text",
        start_dir=tmp_path,
    )

    assert result.provider_id == PROVIDER_CLAUDE
    assert result.executable == "/bin/claude"
    assert result.cwd == tmp_path
    assert result.argv[0] == "/bin/claude"
    assert "--mcp-config" in result.argv
    assert "--session-id" in result.argv
    assert result.argv[-1] == "<prompt>"
    assert result.prompt_supplied is True
    assert "Sensitive prompt text" not in result.argv

    # Verify no process was run
    assert len(runner.invocations) == 0

    # Verify no session was created in DB
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        assert len(store.list_sessions()) == 0


def test_run_success_lifecycle(tmp_path: Path) -> None:
    """Verify successful interactive run transitions session from RUNNING to COMPLETED."""
    _setup_test_project(tmp_path, with_active_task=True)

    runner = FakeProcessRunner(exit_code=0)
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: "/bin/claude",
        is_tty_fn=lambda: True,
    )

    session = service.run(
        provider_name="claude",
        prompt="Execute task",
        start_dir=tmp_path,
    )

    assert session.status == SessionStatus.COMPLETED
    assert session.exit_code == 0
    assert session.exit_reason == SessionExitReason.NORMAL_COMPLETION
    assert session.ended_at is not None

    # Verify process runner was invoked with exact prompt and project root
    assert len(runner.invocations) == 1
    assert runner.invocations[0]["argv"][0] == "/bin/claude"
    assert "--mcp-config" in runner.invocations[0]["argv"]
    assert "--session-id" in runner.invocations[0]["argv"]
    assert runner.invocations[0]["argv"][-1] == "Execute task"
    assert runner.invocations[0]["cwd"] == tmp_path

    # Verify persisted in database
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        saved = store.get_session(session.id)
        assert saved is not None
        assert saved.status == SessionStatus.COMPLETED


def test_run_nonzero_exit_records_failed(tmp_path: Path) -> None:
    """Verify nonzero child process exit marks session as FAILED with exit code preserved."""
    _setup_test_project(tmp_path, with_active_task=True)

    runner = FakeProcessRunner(exit_code=42)
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: "/bin/codex",
        is_tty_fn=lambda: True,
    )

    session = service.run(
        provider_name="codex",
        start_dir=tmp_path,
    )

    assert session.status == SessionStatus.FAILED
    assert session.exit_code == 42
    assert session.exit_reason == SessionExitReason.PROCESS_CRASHED


def test_run_interrupt_records_interrupted(tmp_path: Path) -> None:
    """Verify KeyboardInterrupt marks session as INTERRUPTED and releases workspace lock."""
    _setup_test_project(tmp_path, with_active_task=True)

    runner = FakeProcessRunner(raise_interrupt=True)
    lease_mgr = FileWorkspaceLeaseManager()
    service = RunService(
        process_runner=runner,
        lease_manager=lease_mgr,
        which_fn=lambda _: "/bin/claude",
        is_tty_fn=lambda: True,
    )

    session = service.run(
        provider_name="claude",
        start_dir=tmp_path,
    )

    assert session.status == SessionStatus.INTERRUPTED
    assert session.exit_code == 130
    assert session.exit_reason == SessionExitReason.USER_INTERRUPTED

    # Verify workspace lease was released
    lease = lease_mgr.get_lease(tmp_path)
    assert lease.is_locked() is False


def test_subdirectory_invocation_uses_project_root_cwd(tmp_path: Path) -> None:
    """Verify running from deep subdirectory passes project root as cwd (Same Working Tree)."""
    _setup_test_project(tmp_path, with_active_task=True)

    nested_dir = tmp_path / "src" / "runtime" / "nested"
    nested_dir.mkdir(parents=True)

    runner = FakeProcessRunner(exit_code=0)
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: "/bin/claude",
        is_tty_fn=lambda: True,
    )

    service.run(
        provider_name="claude",
        start_dir=nested_dir,
    )

    assert len(runner.invocations) == 1
    assert runner.invocations[0]["cwd"] == tmp_path


def test_no_active_task_fails_without_spawning(tmp_path: Path) -> None:
    """Verify running when no task is active raises NoActiveTaskError and creates no session."""
    _setup_test_project(tmp_path, with_active_task=False)

    runner = FakeProcessRunner()
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: "/bin/claude",
        is_tty_fn=lambda: True,
    )

    with pytest.raises(NoActiveTaskError):
        service.run(provider_name="claude", start_dir=tmp_path)

    assert len(runner.invocations) == 0
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        assert len(store.list_sessions()) == 0


def test_uninitialized_project_fails(tmp_path: Path) -> None:
    """Verify running in uninitialized directory raises ProjectNotInitializedError."""
    service = RunService(which_fn=lambda _: "/bin/claude")
    with pytest.raises(ProjectNotInitializedError):
        service.run(provider_name="claude", start_dir=tmp_path)


def test_missing_provider_executable_fails(tmp_path: Path) -> None:
    """Verify missing executable raises ProviderNotFoundError and leaves no session."""
    _setup_test_project(tmp_path, with_active_task=True)

    runner = FakeProcessRunner()
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: None,  # Provider missing in PATH
        is_tty_fn=lambda: True,
    )

    with pytest.raises(ProviderNotFoundError) as exc_info:
        service.run(provider_name="claude", start_dir=tmp_path)

    assert "Claude Code was not found in PATH" in str(exc_info.value)
    assert len(runner.invocations) == 0

    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        assert len(store.list_sessions()) == 0


def test_unknown_provider_fails(tmp_path: Path) -> None:
    """Verify unknown provider name raises UnknownProviderError."""
    _setup_test_project(tmp_path, with_active_task=True)

    service = RunService(which_fn=lambda _: "/bin/something")
    with pytest.raises(UnknownProviderError) as exc_info:
        service.run(provider_name="cloude", start_dir=tmp_path)

    assert "Unknown provider 'cloude'" in str(exc_info.value)


def test_non_tty_fails_cleanly(tmp_path: Path) -> None:
    """Verify interactive launch without a TTY raises TerminalRequiredError."""
    _setup_test_project(tmp_path, with_active_task=True)

    runner = FakeProcessRunner()
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: "/bin/claude",
        is_tty_fn=lambda: False,  # Non-TTY
    )

    with pytest.raises(TerminalRequiredError):
        service.run(provider_name="claude", start_dir=tmp_path)

    assert len(runner.invocations) == 0


def test_concurrent_run_rejected_by_workspace_lock(tmp_path: Path) -> None:
    """Verify concurrent run call on locked workspace raises WorkspaceLockedError."""
    _setup_test_project(tmp_path, with_active_task=True)

    lease_mgr = FileWorkspaceLeaseManager()
    lease = lease_mgr.get_lease(tmp_path)
    assert lease.acquire() is True

    try:
        runner = FakeProcessRunner()
        service = RunService(
            process_runner=runner,
            lease_manager=lease_mgr,
            which_fn=lambda _: "/bin/claude",
            is_tty_fn=lambda: True,
        )

        with pytest.raises(WorkspaceLockedError):
            service.run(provider_name="claude", start_dir=tmp_path)

        assert len(runner.invocations) == 0
    finally:
        lease.release()


def test_antigravity_rejects_prompt_in_run(tmp_path: Path) -> None:
    """Verify Antigravity run with prompt raises UnsupportedPromptError."""
    _setup_test_project(tmp_path, with_active_task=True)

    runner = FakeProcessRunner()
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: "/bin/agy",
        is_tty_fn=lambda: True,
    )

    with pytest.raises(UnsupportedPromptError):
        service.run(provider_name="antigravity", prompt="Do work", start_dir=tmp_path)

    assert len(runner.invocations) == 0


def test_stale_running_session_in_db_does_not_block_new_launch(tmp_path: Path) -> None:
    """Verify that a stale running session in SQLite does not prevent a new run."""
    project, task = _setup_test_project(tmp_path, with_active_task=True)
    assert task is not None

    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        # Seed a stale session with status RUNNING
        stale_session = Session(
            task_id=task.id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.RUNNING,
            started_at=utc_now(),
        )
        store.save_session(stale_session)
        assert len(store.list_sessions()) == 1

    runner = FakeProcessRunner(exit_code=0)
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: "/bin/claude",
        is_tty_fn=lambda: True,
    )

    new_session = service.run(provider_name="claude", start_dir=tmp_path)
    assert new_session.status == SessionStatus.COMPLETED
    assert new_session.id != stale_session.id

    with SQLiteStateStore(db_file) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 2


def test_strict_tty_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify TTY requirement checks both stdin and stdout, but dry-run allows non-TTY."""
    import sys

    _setup_test_project(tmp_path, with_active_task=True)

    # 1. stdin=True, stdout=False
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert RunService._check_tty() is False

    # 2. stdin=False, stdout=True
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert RunService._check_tty() is False

    # 3. stdin=True, stdout=True
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    assert RunService._check_tty() is True

    # 4. stdin=False, stdout=False
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert RunService._check_tty() is False

    # Interactive run with default _check_tty (non-TTY) raises TerminalRequiredError
    runner = FakeProcessRunner()
    service = RunService(
        process_runner=runner,
        which_fn=lambda _: "/bin/claude",
    )
    with pytest.raises(TerminalRequiredError):
        service.run(provider_name="claude", start_dir=tmp_path)

    # Dry-run with non-TTY succeeds
    spec = service.dry_run(provider_name="claude", start_dir=tmp_path)
    assert spec.provider_id == PROVIDER_CLAUDE


class FailingProcessRunner(InteractiveProcessRunner):
    """Test runner that simulates process spawn failure (e.g. OSError)."""

    def run_interactive(
        self,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        raise OSError("Permission denied: unable to execute")


def test_spawn_failure_records_failed_and_releases_lease(tmp_path: Path) -> None:
    """Verify that process spawn failure records SPAWN_FAILED, releases lease, and allows retry."""
    _setup_test_project(tmp_path, with_active_task=True)

    failing_runner = FailingProcessRunner()
    lease_mgr = FileWorkspaceLeaseManager()
    service = RunService(
        process_runner=failing_runner,
        lease_manager=lease_mgr,
        which_fn=lambda _: "/bin/claude",
        is_tty_fn=lambda: True,
    )

    with pytest.raises(OSError) as exc_info:
        service.run(provider_name="claude", start_dir=tmp_path)
    assert "Permission denied" in str(exc_info.value)

    # Verify session recorded in DB with status=FAILED, exit_reason=SPAWN_FAILED
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s.status == SessionStatus.FAILED
        assert s.exit_reason == SessionExitReason.SPAWN_FAILED
        assert s.ended_at is not None

    # Verify workspace lease was released
    lease = lease_mgr.get_lease(tmp_path)
    assert lease.acquire() is True
    lease.release()

    # Subsequent run with working runner immediately succeeds
    good_runner = FakeProcessRunner(exit_code=0)
    service_good = RunService(
        process_runner=good_runner,
        lease_manager=lease_mgr,
        which_fn=lambda _: "/bin/claude",
        is_tty_fn=lambda: True,
    )
    retry_session = service_good.run(provider_name="claude", start_dir=tmp_path)
    assert retry_session.status == SessionStatus.COMPLETED
