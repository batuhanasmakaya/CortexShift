"""Unit tests for `cortexshift run` CLI command."""

import json
from pathlib import Path
from typing import Any

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLease
from cortexshift.cli.app import app
from cortexshift.domain.session import SessionStatus
from tests.cli_runner import AnsiFreeCliRunner, unwrapped

runner = AnsiFreeCliRunner()


@pytest.fixture
def active_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fixture to set up an initialized CortexShift project with an active task."""
    monkeypatch.chdir(tmp_path)
    init_res = runner.invoke(app, ["init", "--name", "RunCLIProj"])
    assert init_res.exit_code == 0

    task_res = runner.invoke(
        app,
        [
            "task",
            "start",
            "--title",
            "Build Launch Feature",
            "--objective",
            "Verify provider execution from CLI",
        ],
    )
    assert task_res.exit_code == 0
    return tmp_path


def test_run_help() -> None:
    """Verify `cortexshift run --help` renders provider arguments and options."""
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0
    assert "claude" in result.stdout
    assert "codex" in result.stdout
    assert "antigravity" in result.stdout
    assert "--prompt" in result.stdout or "-p" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--json" in result.stdout


def test_run_json_without_dry_run(active_project: Path) -> None:
    """Verify `--json` without `--dry-run` is rejected with an informative error."""
    result = runner.invoke(app, ["run", "claude", "--json"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "--json is only supported with --dry-run" in output


def test_run_uninitialized_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify running in an uninitialized directory reports friendly error."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["run", "claude", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "CortexShift is not initialized here." in output


def test_run_no_active_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify running without an active task fails cleanly."""
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--name", "NoTaskProj"])

    result = runner.invoke(app, ["run", "claude", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "No active task" in output


def test_run_unknown_provider(active_project: Path) -> None:
    """Verify unrecognized provider identifier is rejected."""
    result = runner.invoke(app, ["run", "cloude", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "Unknown provider 'cloude'" in output
    assert "claude" in output
    assert "codex" in output
    assert "antigravity" in output


def test_run_provider_not_installed(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify missing executable on PATH produces an informative error."""
    monkeypatch.setattr("shutil.which", lambda _: None)

    result = runner.invoke(app, ["run", "claude", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "Claude Code was not found in PATH." in output


def test_run_claude_dry_run_human(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify human-readable dry-run output for Claude Code."""
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
    )

    result = runner.invoke(app, ["run", "claude", "--dry-run"])
    assert result.exit_code == 0
    assert "CortexShift Provider Launch (Dry Run)" in result.stdout
    assert "Claude Code" in result.stdout
    assert "/usr/local/bin/claude" in result.stdout
    assert "Build Launch Feature" in result.stdout
    assert "Prompt" in result.stdout
    assert "none" in result.stdout
    assert "Command: /usr/local/bin/claude" in result.stdout


def test_run_claude_dry_run_with_prompt(
    active_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify prompt is redacted in human-readable dry-run output."""
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
    )

    result = runner.invoke(app, ["run", "claude", "-p", "Secret user instruction", "--dry-run"])
    assert result.exit_code == 0
    assert "Prompt" in result.stdout
    assert "supplied" in result.stdout
    assert "Command: /usr/local/bin/claude" in result.stdout
    assert "--mcp-config" in result.stdout
    assert "--session-id" in result.stdout
    assert "<prompt>" in result.stdout
    assert "Secret user instruction" not in result.stdout


def test_run_claude_dry_run_json(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `--dry-run --json` outputs parseable JSON with redacted prompt."""
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
    )

    result = runner.invoke(
        app,
        ["run", "claude", "--prompt", "Super confidential task", "--dry-run", "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["provider_id"] == "claude"
    assert data["display_name"] == "Claude Code"
    assert data["executable"] == "/usr/local/bin/claude"
    assert data["prompt_supplied"] is True
    assert data["argv"][0] == "/usr/local/bin/claude"
    assert "--mcp-config" in data["argv"]
    assert "--session-id" in data["argv"]
    assert data["argv"][-1] == "<prompt>"
    assert data["task_title"] == "Build Launch Feature"
    assert "Super confidential task" not in result.stdout


def test_run_codex_dry_run(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify dry-run for Codex with and without prompt."""
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/opt/homebrew/bin/codex" if cmd == "codex" else None,
    )

    # Without prompt
    res1 = runner.invoke(app, ["run", "codex", "--dry-run"])
    assert res1.exit_code == 0
    assert "Codex" in res1.stdout
    assert "Command: /opt/homebrew/bin/codex" in res1.stdout
    assert "-c" in res1.stdout

    # With prompt
    res2 = runner.invoke(app, ["run", "codex", "-p", "Investigate logs", "--dry-run"])
    assert res2.exit_code == 0
    assert "Command: /opt/homebrew/bin/codex" in res2.stdout
    assert "<prompt>" in res2.stdout
    assert "Investigate logs" not in res2.stdout


def test_run_antigravity_dry_run(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify dry-run for Antigravity without prompt."""
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/Users/user/.antigravity/bin/agy" if cmd == "agy" else None,
    )

    result = runner.invoke(app, ["run", "antigravity", "--dry-run"])
    assert result.exit_code == 0
    assert "Antigravity" in result.stdout
    assert "Command: /Users/user/.antigravity/bin/agy" in result.stdout


def test_run_antigravity_rejects_prompt(
    active_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify initial prompt for Antigravity is cleanly rejected."""
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/Users/user/.antigravity/bin/agy" if cmd == "agy" else None,
    )

    result = runner.invoke(app, ["run", "antigravity", "--prompt", "Initial prompt", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "Antigravity does not currently expose a supported interactive" in unwrapped(output)


def test_run_terminal_required(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify non-TTY interactive launch is rejected with clear error."""
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
    )

    result = runner.invoke(app, ["run", "claude"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "Interactive provider launch requires a terminal" in output
    assert "cortexshift run claude" in output
    assert "--dry-run" in output


def test_run_interactive_success(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify interactive provider run executes, updates session to completed, and exits 0."""
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
    )

    invoked_args: list[list[str]] = []

    def mock_run_interactive(
        self: Any,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        invoked_args.append(argv)
        return 0

    monkeypatch.setattr(
        "cortexshift.adapters.process_runner.SubprocessInteractiveProcessRunner.run_interactive",
        mock_run_interactive,
    )

    result = runner.invoke(app, ["run", "claude"])
    assert result.exit_code == 0
    assert "Launching native provider..." in result.stdout
    assert "Session completed." in result.stdout
    assert len(invoked_args) == 1
    assert invoked_args[0][0] == "/usr/local/bin/claude"
    assert "--mcp-config" in invoked_args[0]
    assert "--session-id" in invoked_args[0]
    assert len(invoked_args[0]) == 5

    # Verify session persisted in DB
    db_path = active_project / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.COMPLETED
        assert sessions[0].exit_code == 0


def test_run_interactive_failure(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify provider exit code > 0 updates session to failed and bubbles exit code."""
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
    )

    def mock_run_interactive(
        self: Any,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        return 42

    monkeypatch.setattr(
        "cortexshift.adapters.process_runner.SubprocessInteractiveProcessRunner.run_interactive",
        mock_run_interactive,
    )

    result = runner.invoke(app, ["run", "claude"])
    assert result.exit_code == 42
    assert "Session failed." in result.stdout

    # Verify session recorded in DB with exit code 42
    db_path = active_project / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.FAILED
        assert sessions[0].exit_code == 42


def test_run_interactive_interrupted(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify KeyboardInterrupt updates session to interrupted."""
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
    )

    def mock_run_interactive(
        self: Any,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        raise KeyboardInterrupt()

    monkeypatch.setattr(
        "cortexshift.adapters.process_runner.SubprocessInteractiveProcessRunner.run_interactive",
        mock_run_interactive,
    )

    result = runner.invoke(app, ["run", "claude"])
    assert result.exit_code == 0
    assert "Session interrupted." in result.stdout

    db_path = active_project / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].status == SessionStatus.INTERRUPTED


def test_run_workspace_locked(active_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify locked workspace prevents second agent launch."""
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        "shutil.which",
        lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None,
    )

    lock_path = active_project / ".cortexshift" / "agent.lock"
    lease = FileWorkspaceLease(lock_path)
    assert lease.acquire() is True

    try:
        result = runner.invoke(app, ["run", "claude"])
        assert result.exit_code == 1
        output = result.stderr + result.stdout
        assert "Another CortexShift agent session is already active" in output
    finally:
        lease.release()
