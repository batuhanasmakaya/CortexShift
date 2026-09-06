"""Unit tests for `cortexshift session` CLI commands."""

import json
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.cli.app import app
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from tests.cli_runner import AnsiFreeCliRunner

runner = AnsiFreeCliRunner()


@pytest.fixture
def initialized_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, str, str]:
    """Fixture to create an initialized project with an active task."""
    monkeypatch.chdir(tmp_path)
    init_res = runner.invoke(app, ["init", "--name", "SessionCLIProj"])
    assert init_res.exit_code == 0

    db_path = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        proj = store.get_default_project()
        assert proj is not None
        task = Task(
            project_id=proj.id, title="CLI Session Task", objective="Test session inspection"
        )
        store.save_task(task)
        store.set_active_task_id(proj.id, task.id)
        return tmp_path, proj.id, task.id


def test_session_help() -> None:
    """Verify `cortexshift session --help` lists subcommands."""
    result = runner.invoke(app, ["session", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "show" in result.stdout


def test_session_list_uninitialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `cortexshift session list` outside project reports uninitialized."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["session", "list"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "CortexShift is not initialized here." in output


def test_session_list_empty(initialized_project: tuple[Path, str, str]) -> None:
    """Verify `cortexshift session list` when no sessions exist."""
    result = runner.invoke(app, ["session", "list"])
    assert result.exit_code == 0
    assert "No sessions found." in result.stdout


def test_session_list_empty_json(initialized_project: tuple[Path, str, str]) -> None:
    """Verify `cortexshift session list --json` when no sessions exist outputs empty array."""
    result = runner.invoke(app, ["session", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data == []


def test_session_list_populated(
    initialized_project: tuple[Path, str, str], fixed_console_width: int
) -> None:
    """Verify `cortexshift session list` displays formatted session table."""
    tmp_path, proj_id, task_id = initialized_project
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"

    s1 = Session(
        task_id=task_id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.COMPLETED,
        started_at=utc_now(),
        ended_at=utc_now(),
        exit_reason=SessionExitReason.NORMAL_COMPLETION,
        exit_code=0,
    )
    s2 = Session(
        task_id=task_id,
        provider_id=PROVIDER_CODEX,
        status=SessionStatus.RUNNING,
        started_at=utc_now(),
    )
    s3 = Session(
        task_id=task_id,
        provider_id=PROVIDER_ANTIGRAVITY,
        status=SessionStatus.FAILED,
        started_at=utc_now(),
        ended_at=utc_now(),
        exit_reason=SessionExitReason.PROCESS_CRASHED,
        exit_code=1,
    )

    with SQLiteStateStore(db_path) as store:
        store.save_session(s1)
        store.save_session(s2)
        store.save_session(s3)

    result = runner.invoke(app, ["session", "list"])
    assert result.exit_code == 0
    assert "Agent Sessions" in result.stdout
    assert "Claude Code" in result.stdout
    assert "Codex" in result.stdout
    assert "Antigravity" in result.stdout
    assert "completed" in result.stdout
    assert "running" in result.stdout
    assert "failed" in result.stdout
    assert s1.id[:10] in result.stdout
    assert s2.id[:10] in result.stdout
    assert s3.id[:10] in result.stdout


def test_session_list_limit(initialized_project: tuple[Path, str, str]) -> None:
    """Verify `--limit` caps the returned sessions."""
    tmp_path, proj_id, task_id = initialized_project
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"

    with SQLiteStateStore(db_path) as store:
        for _ in range(5):
            s = Session(
                task_id=task_id,
                provider_id=PROVIDER_CLAUDE,
                status=SessionStatus.COMPLETED,
                started_at=utc_now(),
            )
            store.save_session(s)

    result = runner.invoke(app, ["session", "list", "--limit", "2"])
    assert result.exit_code == 0
    lines = [line for line in result.stdout.splitlines() if "sess_" in line]
    assert len(lines) == 2


def test_session_list_json(initialized_project: tuple[Path, str, str]) -> None:
    """Verify `cortexshift session list --json` outputs parseable list."""
    tmp_path, proj_id, task_id = initialized_project
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"

    session = Session(
        task_id=task_id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.COMPLETED,
        started_at=utc_now(),
        ended_at=utc_now(),
        exit_reason=SessionExitReason.NORMAL_COMPLETION,
        exit_code=0,
        native_session_id="claude-native-123",
    )

    with SQLiteStateStore(db_path) as store:
        store.save_session(session)

    result = runner.invoke(app, ["session", "list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == session.id
    assert data[0]["provider_id"] == "claude"
    assert data[0]["status"] == "completed"
    assert data[0]["native_session_id"] == "claude-native-123"
    assert data[0]["exit_code"] == 0


def test_session_show_help() -> None:
    """Verify `cortexshift session show --help`."""
    result = runner.invoke(app, ["session", "show", "--help"])
    assert result.exit_code == 0
    assert "session_id" in result.stdout or "SESSION_ID" in result.stdout
    assert "--json" in result.stdout


def test_session_show_uninitialized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify `cortexshift session show` outside project fails cleanly."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["session", "show", "sess_nonexistent"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "CortexShift is not initialized here." in output


def test_session_show_not_found(initialized_project: tuple[Path, str, str]) -> None:
    """Verify showing a nonexistent session ID exits 1 with clean error."""
    result = runner.invoke(app, ["session", "show", "sess_999999999999"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "Session 'sess_999999999999' was not found." in output


def test_session_show_human(
    initialized_project: tuple[Path, str, str], fixed_console_width: int
) -> None:
    """Verify human-readable `cortexshift session show` grid output."""
    tmp_path, proj_id, task_id = initialized_project
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"

    session = Session(
        task_id=task_id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.COMPLETED,
        started_at=utc_now(),
        ended_at=utc_now(),
        exit_reason=SessionExitReason.NORMAL_COMPLETION,
        exit_code=0,
        native_session_id="native-abc-987",
    )

    with SQLiteStateStore(db_path) as store:
        store.save_session(session)

    result = runner.invoke(app, ["session", "show", session.id])
    assert result.exit_code == 0
    assert f"Session: {session.id}" in result.stdout
    assert "Claude Code" in result.stdout
    assert task_id in result.stdout
    assert "completed" in result.stdout
    assert "native-abc-987" in result.stdout
    assert "normal_completion" in result.stdout
    assert "0" in result.stdout


def test_session_show_json(initialized_project: tuple[Path, str, str]) -> None:
    """Verify `cortexshift session show <id> --json` outputs parseable JSON dict."""
    tmp_path, proj_id, task_id = initialized_project
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"

    session = Session(
        task_id=task_id,
        provider_id=PROVIDER_CODEX,
        status=SessionStatus.FAILED,
        started_at=utc_now(),
        ended_at=utc_now(),
        exit_reason=SessionExitReason.PROCESS_CRASHED,
        exit_code=137,
    )

    with SQLiteStateStore(db_path) as store:
        store.save_session(session)

    result = runner.invoke(app, ["session", "show", session.id, "--json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["id"] == session.id
    assert data["provider_id"] == "codex"
    assert data["status"] == "failed"
    assert data["exit_code"] == 137
    assert data["exit_reason"] == "process_crashed"
