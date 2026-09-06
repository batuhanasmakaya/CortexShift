"""Unit tests for the `cortexshift switch` CLI command."""

import json
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.switch_service import SwitchService
from cortexshift.cli.app import app
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import SessionExitReason, SessionStatus
from tests.cli_runner import AnsiFreeCliRunner, unwrapped
from tests.factories import FakeCodexBootstrap, patch_which, seed_project, seed_session

runner = AnsiFreeCliRunner()


@pytest.fixture
def switchable_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Project with an active task and one completed Claude session to hand off from."""
    monkeypatch.chdir(tmp_path)
    _, task = seed_project(
        tmp_path,
        project_name="SwitchCLIProj",
        completed=["Scaffolded auth module"],
        remaining=["Wire token refresh"],
        current_work="Implementing token exchange",
    )
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)
    return tmp_path


@pytest.fixture
def stub_executables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every provider executable without requiring a real install."""
    patch_which(monkeypatch, "cortexshift.application.switch_service", lambda cmd: f"/bin/{cmd}")


def test_switch_help() -> None:
    """Verify `cortexshift switch --help` documents targets, options, and bootstrap."""
    result = runner.invoke(app, ["switch", "--help"])
    assert result.exit_code == 0

    output = result.stdout
    assert "claude" in output
    assert "codex" in output
    assert "antigravity" in output
    assert "--from-session" in output
    assert "--note" in output
    assert "--dry-run" in output
    assert "--json" in output
    # The Antigravity bootstrap model turn must be documented, not hidden.
    assert "read-only" in output.lower()
    assert "read-only" in output.lower()


def test_switch_json_requires_dry_run(switchable_project: Path) -> None:
    """Verify `--json` without `--dry-run` is rejected clearly."""
    result = runner.invoke(app, ["switch", "codex", "--json"])
    assert result.exit_code == 1
    assert "--json is only supported with --dry-run" in result.stderr + result.stdout


def test_switch_uninitialized_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify an uninitialized directory produces guidance, not a traceback."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["switch", "codex", "--dry-run"])

    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "cortexshift init" in output
    assert "Traceback" not in output


def test_switch_without_active_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify a project without an active task fails with actionable guidance."""
    monkeypatch.chdir(tmp_path)
    project, _ = seed_project(tmp_path)
    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3", auto_migrate=False) as store:
        store.set_active_task_id(project.id, None)

    result = runner.invoke(app, ["switch", "codex", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "cortexshift task start" in output
    assert "Traceback" not in output


def test_switch_without_source_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify switch tells the user to run the first agent instead of guessing."""
    monkeypatch.chdir(tmp_path)
    seed_project(tmp_path)

    result = runner.invoke(app, ["switch", "codex", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "cortexshift run <provider>" in output
    assert "Traceback" not in output


def test_switch_same_provider_rejected(switchable_project: Path, stub_executables: None) -> None:
    """Verify switching to the provider already in use is refused."""
    result = runner.invoke(app, ["switch", "claude", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "already uses Claude Code" in output
    assert "Traceback" not in output


def test_switch_unknown_provider_rejected(switchable_project: Path) -> None:
    """Verify an unknown target names the supported providers."""
    result = runner.invoke(app, ["switch", "bogus", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "Unknown provider 'bogus'" in output
    assert "antigravity, claude, codex" in output
    assert "Traceback" not in output


def test_switch_missing_target_provider(
    switchable_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a target provider that is not installed fails before any delivery."""
    patch_which(monkeypatch, "cortexshift.application.switch_service", lambda _cmd: None)
    result = runner.invoke(app, ["switch", "codex", "--dry-run"])

    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "was not found in PATH" in output
    assert "Traceback" not in output


def test_switch_bad_source_session(switchable_project: Path, stub_executables: None) -> None:
    """Verify an unknown explicit source session is rejected cleanly."""
    result = runner.invoke(app, ["switch", "codex", "--from-session", "sess_ghost", "--dry-run"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "sess_ghost" in output
    assert "Traceback" not in output


def test_switch_dry_run_human_output(switchable_project: Path, stub_executables: None) -> None:
    """Verify the human dry run reports the plan and states nothing happened."""
    result = runner.invoke(app, ["switch", "codex", "--dry-run"])

    assert result.exit_code == 0
    output = result.stdout
    assert "CortexShift Handoff Protocol v1" in output
    assert "Claude Code" in output
    assert "Codex" in output
    assert "read_only_bootstrap_then_resume" in output
    assert "Bootstrap model turn" in output
    assert "Context size" in output
    assert "nothing was persisted and nothing was launched" in output


def test_switch_dry_run_json_contract(switchable_project: Path, stub_executables: None) -> None:
    """Verify machine-readable dry-run output uses canonical IDs and enum values."""
    result = runner.invoke(app, ["switch", "codex", "--dry-run", "--json"])
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data["protocol_version"] == 1
    assert data["source_provider_id"] == "claude"
    assert data["target_provider_id"] == "codex"
    assert data["source_session_id"].startswith("sess_")
    assert data["task_id"].startswith("task_")
    assert data["project_id"].startswith("proj_")
    assert data["delivery_strategy"] == "read_only_bootstrap_then_resume"
    assert data["bootstrap_model_turn_required"] is True
    assert data["context_truncated"] is False
    assert "\x1b[" not in result.stdout

    with SQLiteStateStore(
        switchable_project / ".cortexshift" / "state.sqlite3", auto_migrate=False
    ) as store:
        assert store.list_handoffs() == []
        assert len(store.list_sessions()) == 1


def test_switch_dry_run_antigravity_reports_bootstrap_turn(
    switchable_project: Path, stub_executables: None
) -> None:
    """Verify an Antigravity dry run warns that a model turn would occur, without running it."""
    human = runner.invoke(app, ["switch", "antigravity", "--dry-run"])
    assert human.exit_code == 0
    assert "plan_bootstrap_then_resume" in human.stdout
    assert "one read-only planning turn would run" in unwrapped(human.stdout)

    machine = runner.invoke(app, ["switch", "antigravity", "--dry-run", "--json"])
    data = json.loads(machine.stdout)
    assert data["bootstrap_model_turn_required"] is True
    assert data["delivery_strategy"] == "plan_bootstrap_then_resume"


def test_switch_dry_run_does_not_require_a_tty(
    switchable_project: Path, stub_executables: None
) -> None:
    """Verify dry run works in a non-interactive environment."""
    assert runner.invoke(app, ["switch", "codex", "--dry-run"]).exit_code == 0


def test_switch_without_tty_rejected(switchable_project: Path, stub_executables: None) -> None:
    """Verify an actual switch refuses to run without an interactive terminal."""
    result = runner.invoke(app, ["switch", "codex"])

    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "requires a terminal" in output
    assert "--dry-run" in output
    assert "Traceback" not in output


def test_switch_workspace_locked(
    switchable_project: Path, stub_executables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify an active agent session blocks switching without lock-file deletion advice."""
    monkeypatch.setattr(SwitchService, "_check_tty", staticmethod(lambda: True))
    lease = FileWorkspaceLeaseManager().get_lease(switchable_project)
    assert lease.acquire() is True
    try:
        result = runner.invoke(app, ["switch", "codex"])
    finally:
        lease.release()

    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "Another CortexShift agent session is already active" in output
    assert "delete" not in output.lower()
    assert "Traceback" not in output


def test_switch_note_reaches_the_receiving_agent(
    switchable_project: Path,
    stub_executables: None,
    monkeypatch: pytest.MonkeyPatch,
    codex_bootstrap: FakeCodexBootstrap,
) -> None:
    """Verify `--note` is delivered as attributed advisory context."""
    captured: list[list[str]] = []

    class RecordingRunner:
        def run_interactive(
            self,
            argv: list[str],
            cwd: Path | str,
            env: dict[str, str] | None = None,
        ) -> int:
            captured.append(argv)
            return 0

    monkeypatch.setattr(SwitchService, "_check_tty", staticmethod(lambda: True))
    monkeypatch.setattr(
        "cortexshift.application.switch_service.SubprocessInteractiveProcessRunner",
        RecordingRunner,
    )

    result = runner.invoke(app, ["switch", "codex", "--note", "Mind the flaky integration test"])

    assert result.exit_code == 0
    assert "Session completed." in result.stdout
    assert len(captured) == 1
    context = codex_bootstrap.invocations[-1][-1]
    assert "## OPERATOR NOTE" in context
    assert "Mind the flaky integration test" in context


def test_switch_reports_failed_target_session(
    switchable_project: Path, stub_executables: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a receiving provider that exits non-zero surfaces as a failed session."""

    class FailingRunner:
        def run_interactive(
            self,
            argv: list[str],
            cwd: Path | str,
            env: dict[str, str] | None = None,
        ) -> int:
            return 3

    monkeypatch.setattr(SwitchService, "_check_tty", staticmethod(lambda: True))
    monkeypatch.setattr(
        "cortexshift.application.switch_service.SubprocessInteractiveProcessRunner",
        FailingRunner,
    )

    result = runner.invoke(app, ["switch", "codex"])

    assert result.exit_code == 3
    assert "Session failed." in result.stdout
    # Delivery succeeded even though the session did not.
    assert "was delivered" in unwrapped(result.stdout)

    with SQLiteStateStore(
        switchable_project / ".cortexshift" / "state.sqlite3", auto_migrate=False
    ) as store:
        handoffs = store.list_handoffs()
        assert len(handoffs) == 1
        target = store.get_session(str(handoffs[0].target_session_id))
        assert target is not None
        assert target.status == SessionStatus.FAILED
        assert target.exit_reason == SessionExitReason.PROCESS_CRASHED
