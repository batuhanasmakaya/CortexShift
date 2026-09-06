"""Integration tests for native provider run lifecycle, lease, and session persistence."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLease
from cortexshift.cli.app import app
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus

runner = CliRunner()


@pytest.fixture(autouse=True)
def portable_python_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run Python fakes portably; Windows does not execute POSIX shebangs."""
    original = subprocess.Popen

    def launch(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if isinstance(argv, list) and argv and str(argv[0]).endswith(".py"):
            argv = [sys.executable, *argv]
        return original(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", launch)


def _create_fake_provider_script(tmp_path: Path, filename: str, exit_code: int = 0) -> Path:
    """Create an executable Python script that records its execution context to a JSON file."""
    script_path = tmp_path / filename
    script_content = f"""#!{sys.executable}
import json
import os
import sys
from pathlib import Path

report_file = Path(os.environ.get("CORTEXSHIFT_TEST_REPORT", {str(tmp_path / "report.json")!r}))
report_file.parent.mkdir(parents=True, exist_ok=True)
report_file.write_text(
    json.dumps(
        {{
            "cwd": os.getcwd(),
            "argv": sys.argv,
        }}
    )
)
sys.exit({exit_code})
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)
    return script_path


@pytest.fixture
def git_repo_with_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fixture creating a real Git repository, CortexShift project, and an active task."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    readme = tmp_path / "README.md"
    readme.write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    monkeypatch.chdir(tmp_path)
    init_res = runner.invoke(app, ["init", "--name", "LifecycleProj"])
    assert init_res.exit_code == 0

    task_res = runner.invoke(
        app,
        [
            "task",
            "start",
            "--title",
            "Lifecycle Task",
            "--objective",
            "Test end to end provider launch",
        ],
    )
    assert task_res.exit_code == 0
    return tmp_path


def test_end_to_end_run_from_nested_subdirectory(
    git_repo_with_task: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify provider launched from nested directory executes at project root."""
    report_file = git_repo_with_task / "claude_exec_report.json"
    fake_claude = _create_fake_provider_script(git_repo_with_task, "fake_claude.py", exit_code=0)

    monkeypatch.setenv("CORTEXSHIFT_TEST_REPORT", str(report_file))
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: str(fake_claude) if cmd == "claude" else None,
    )

    # Change into deeply nested directory
    nested_dir = git_repo_with_task / "a" / "b" / "deep" / "dir"
    nested_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(nested_dir)

    # Launch Claude from nested dir
    result = runner.invoke(app, ["run", "claude"])
    assert result.exit_code == 0
    assert "Launching native provider..." in result.stdout
    assert "Session completed." in result.stdout

    # Verify provider executed with cwd == project_root (not nested_dir)
    assert report_file.exists()
    report_data = json.loads(report_file.read_text())
    assert Path(report_data["cwd"]).resolve() == git_repo_with_task.resolve()

    # Verify session persisted in SQLite DB
    db_path = git_repo_with_task / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert report_data["argv"][0] == str(fake_claude)
        assert "--mcp-config" in report_data["argv"]
        sess_arg_idx = report_data["argv"].index("--session-id") + 1
        assert report_data["argv"][sess_arg_idx] == s.native_session_id
        assert s.provider_id == "claude"
        assert s.status == SessionStatus.COMPLETED
        assert s.exit_code == 0
        assert s.exit_reason == SessionExitReason.NORMAL_COMPLETION

    # Verify session is queryable via CLI
    list_res = runner.invoke(app, ["session", "list"])
    assert list_res.exit_code == 0
    assert "Claude Code" in list_res.stdout
    assert "completed" in list_res.stdout

    show_res = runner.invoke(app, ["session", "show", s.id])
    assert show_res.exit_code == 0
    assert "Claude Code" in show_res.stdout
    assert "completed" in show_res.stdout
    assert "0" in show_res.stdout

    # Verify lock was cleanly released
    lease = FileWorkspaceLease(git_repo_with_task / ".cortexshift" / "agent.lock")
    assert lease.acquire() is True
    lease.release()


def test_process_failure_lifecycle(
    git_repo_with_task: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify non-zero provider exit propagates exit code and records failed session."""
    report_file = git_repo_with_task / "codex_fail_report.json"
    fake_codex = _create_fake_provider_script(git_repo_with_task, "fake_codex.py", exit_code=17)

    monkeypatch.setenv("CORTEXSHIFT_TEST_REPORT", str(report_file))
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: str(fake_codex) if cmd == "codex" else None,
    )

    result = runner.invoke(app, ["run", "codex"])
    assert result.exit_code == 17
    assert "Session failed." in result.stdout

    db_path = git_repo_with_task / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        s = sessions[0]
        assert s.provider_id == "codex"
        assert s.status == SessionStatus.FAILED
        assert s.exit_code == 17
        assert s.exit_reason == SessionExitReason.PROCESS_CRASHED

    # Verify lock released
    lease = FileWorkspaceLease(git_repo_with_task / ".cortexshift" / "agent.lock")
    assert lease.acquire() is True
    lease.release()


def test_workspace_lease_exclusion_and_cleanup(
    git_repo_with_task: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify exclusive workspace lease prevents concurrent execution and cleans up properly."""
    fake_claude = _create_fake_provider_script(git_repo_with_task, "fake_claude.py", exit_code=0)
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: str(fake_claude) if cmd == "claude" else None,
    )

    lock_file = git_repo_with_task / ".cortexshift" / "agent.lock"
    external_lease = FileWorkspaceLease(lock_file)
    assert external_lease.acquire() is True

    try:
        # Concurrent attempt must fail immediately
        result = runner.invoke(app, ["run", "claude"])
        assert result.exit_code == 1
        output = result.stderr + result.stdout
        assert "Another CortexShift agent session is already active" in output

        # No session was saved for rejected attempt
        db_path = git_repo_with_task / ".cortexshift" / "state.sqlite3"
        with SQLiteStateStore(db_path) as store:
            assert len(store.list_sessions()) == 0
    finally:
        external_lease.release()

    # Now that lock is released, launch succeeds
    res_ok = runner.invoke(app, ["run", "claude"])
    assert res_ok.exit_code == 0
    assert "Session completed." in res_ok.stdout


def test_session_durability_across_store_reopen(
    git_repo_with_task: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify sessions written to disk survive across fresh store instances and reopens."""
    fake_claude = _create_fake_provider_script(git_repo_with_task, "fake_claude.py", exit_code=0)
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: str(fake_claude) if cmd == "claude" else None,
    )

    # Run session 1
    res1 = runner.invoke(app, ["run", "claude", "-p", "Task 1"])
    assert res1.exit_code == 0

    # Run session 2
    res2 = runner.invoke(app, ["run", "claude", "-p", "Task 2"])
    assert res2.exit_code == 0

    db_path = git_repo_with_task / ".cortexshift" / "state.sqlite3"

    # Reopen database in completely independent SQLiteStateStore instance
    with SQLiteStateStore(db_path, auto_migrate=False) as fresh_store:
        sessions = fresh_store.list_sessions()
        assert len(sessions) == 2
        for s in sessions:
            assert s.status == SessionStatus.COMPLETED
            assert s.exit_code == 0
            assert s.ended_at is not None
            assert s.exit_reason == SessionExitReason.NORMAL_COMPLETION

    # Verify session list via CLI
    list_res = runner.invoke(app, ["session", "list", "--json"])
    assert list_res.exit_code == 0
    data = json.loads(list_res.stdout)
    assert len(data) == 2


def test_stale_running_session_does_not_block_cli_run(
    git_repo_with_task: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify ungracefully terminated session in DB does not block subsequent CLI runs."""
    fake_claude = _create_fake_provider_script(git_repo_with_task, "fake_claude.py", exit_code=0)
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: str(fake_claude) if cmd == "claude" else None,
    )

    # Inject a stale running session in SQLite
    db_path = git_repo_with_task / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        project = store.get_default_project()
        assert project is not None
        active_task_id = store.get_active_task_id(project.id)
        assert active_task_id is not None
        stale_sess = Session(
            task_id=active_task_id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.RUNNING,
            started_at=utc_now(),
        )
        store.save_session(stale_sess)

    # Next CLI run must succeed without error
    result = runner.invoke(app, ["run", "claude"])
    assert result.exit_code == 0
    assert "Session completed." in result.stdout

    # Verify both sessions exist
    with SQLiteStateStore(db_path) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 2


def test_spawn_failure_end_to_end(
    git_repo_with_task: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify that process spawn failure records SPAWN_FAILED, releases lease, and allows retry."""
    from typing import Any

    def mock_run_interactive(
        self: Any,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        raise OSError("Simulated spawn execution error")

    monkeypatch.setattr(
        "cortexshift.adapters.process_runner.SubprocessInteractiveProcessRunner.run_interactive",
        mock_run_interactive,
    )
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/bin/claude" if cmd == "claude" else None,
    )

    result = runner.invoke(app, ["run", "claude"])
    assert result.exit_code == 1
    assert "Simulated spawn execution error" in (result.stderr + result.stdout)

    db_path = git_repo_with_task / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.FAILED
        assert sessions[0].exit_reason == SessionExitReason.SPAWN_FAILED

    # Verify workspace lease was released
    lock_file = git_repo_with_task / ".cortexshift" / "agent.lock"
    lease = FileWorkspaceLease(lock_file)
    assert lease.acquire() is True
    lease.release()
