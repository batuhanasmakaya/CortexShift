"""Unit tests for the `cortexshift handoff` CLI command group."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.cli.app import app
from cortexshift.domain.handoff import HandoffRecord, HandoffStatus
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX
from tests.factories import make_payload, patch_which, seed_project, seed_session

runner = CliRunner()


@pytest.fixture
def handoff_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Project with an active task and a prior Claude session."""
    monkeypatch.chdir(tmp_path)
    seed_project(
        tmp_path,
        project_name="HandoffCLIProj",
        completed=["Scaffolded auth module"],
        remaining=["Wire token refresh"],
        current_work="Implementing token exchange",
    )
    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3", auto_migrate=False) as store:
        project = store.get_default_project()
        assert project is not None
        task_id = store.get_active_task_id(project.id)
    assert task_id is not None
    seed_session(tmp_path, task_id, provider_id=PROVIDER_CLAUDE)
    return tmp_path


def _persist_handoff(root: Path, **overrides: object) -> HandoffRecord:
    """Persist a canonical handoff record directly for history tests."""
    db_file = root / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        project = store.get_default_project()
        assert project is not None
        task_id = store.get_active_task_id(project.id)
        assert task_id is not None
        session = store.list_sessions(task_id=task_id)[0]

        defaults: dict[str, object] = {
            "project_id": project.id,
            "task_id": task_id,
            "source_session_id": session.id,
            "source_provider_id": PROVIDER_CLAUDE,
            "target_provider_id": PROVIDER_CODEX,
            "payload": make_payload(),
        }
        defaults.update(overrides)
        record = HandoffRecord(**defaults)  # type: ignore[arg-type]
        store.save_handoff(record)
    return record


def test_handoff_help() -> None:
    """Verify the handoff group exposes preview, list, and show."""
    result = runner.invoke(app, ["handoff", "--help"])
    assert result.exit_code == 0
    assert "preview" in result.stdout
    assert "list" in result.stdout
    assert "show" in result.stdout


@pytest.mark.parametrize("subcommand", ["preview", "list", "show"])
def test_handoff_subcommand_help(subcommand: str) -> None:
    """Verify each handoff subcommand renders its own help."""
    result = runner.invoke(app, ["handoff", subcommand, "--help"])
    assert result.exit_code == 0
    assert "--json" in result.stdout


# --- preview ---


def test_handoff_preview_human_output(handoff_project: Path) -> None:
    """Verify preview renders the canonical context and marks itself as a preview."""
    result = runner.invoke(app, ["handoff", "preview", "codex"])

    assert result.exit_code == 0
    output = result.stdout
    assert "CortexShift Handoff Preview" in output
    assert "Preview only" in output
    assert "become stale" in output
    assert "CORTEXSHIFT HANDOFF PROTOCOL v1" in output
    assert "AUTHORITY ORDER" in output
    assert "Add OAuth2 PKCE support" in output


def test_handoff_preview_persists_nothing(handoff_project: Path) -> None:
    """Verify preview creates no handoff, snapshot, or session."""
    assert runner.invoke(app, ["handoff", "preview", "codex"]).exit_code == 0

    with SQLiteStateStore(
        handoff_project / ".cortexshift" / "state.sqlite3", auto_migrate=False
    ) as store:
        project = store.get_default_project()
        assert project is not None
        assert store.list_handoffs() == []
        assert store.list_snapshots(project_id=project.id) == []
        assert len(store.list_sessions()) == 1


def test_handoff_preview_needs_no_installed_provider(
    handoff_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify preview works even when no provider CLI is installed."""
    patch_which(monkeypatch, "cortexshift.application.switch_service", lambda _cmd: None)
    result = runner.invoke(app, ["handoff", "preview", "antigravity"])
    assert result.exit_code == 0
    assert "CORTEXSHIFT HANDOFF PROTOCOL v1" in result.stdout


def test_handoff_preview_json_contract(handoff_project: Path) -> None:
    """Verify preview JSON exposes the canonical payload and rendering metadata."""
    result = runner.invoke(app, ["handoff", "preview", "codex", "--json"])
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data["protocol_version"] == 1
    assert data["target_provider_id"] == "codex"
    assert data["delivery_strategy"] == "read_only_bootstrap_then_resume"
    assert data["bootstrap_model_turn_required"] is True
    assert data["payload"]["original_objective"].startswith("Add OAuth2 PKCE")
    assert data["payload"]["source_session"]["provider_id"] == "claude"
    assert data["payload"]["test_status"]["known"] is False
    assert data["payload"]["decisions_known"] is False
    assert data["context_truncated"] is False
    assert "CORTEXSHIFT HANDOFF PROTOCOL v1" in data["rendered_context"]
    assert "\x1b[" not in result.stdout


def test_handoff_preview_errors_are_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify preview surfaces domain errors without tracebacks."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["handoff", "preview", "codex"])
    assert result.exit_code == 1
    assert "cortexshift init" in result.stderr + result.stdout
    assert "Traceback" not in result.stderr + result.stdout


# --- list ---


def test_handoff_list_empty(handoff_project: Path) -> None:
    """Verify an empty history renders a friendly message."""
    result = runner.invoke(app, ["handoff", "list"])
    assert result.exit_code == 0
    assert "No handoffs recorded yet." in result.stdout


def test_handoff_list_human_table(handoff_project: Path) -> None:
    """Verify listing shows source, target, status, and creation time."""
    _persist_handoff(handoff_project)
    _persist_handoff(
        handoff_project,
        source_provider_id=PROVIDER_CODEX,
        target_provider_id=PROVIDER_ANTIGRAVITY,
        status=HandoffStatus.DELIVERED,
    )

    result = runner.invoke(app, ["handoff", "list"])
    assert result.exit_code == 0
    output = result.stdout
    assert "Handoff" in output
    assert "Claude Code" in output
    assert "Codex" in output
    assert "Antigravity" in output
    assert "delivered" in output


def test_handoff_list_json_contract(handoff_project: Path) -> None:
    """Verify list JSON uses full IDs, canonical providers, and ISO timestamps."""
    record = _persist_handoff(handoff_project)

    result = runner.invoke(app, ["handoff", "list", "--json"])
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert len(data) == 1
    assert data[0]["id"] == record.id
    assert data[0]["protocol_version"] == 1
    assert data[0]["source_provider_id"] == "claude"
    assert data[0]["target_provider_id"] == "codex"
    assert data[0]["status"] == "prepared"
    assert data[0]["created_at"].endswith("Z") or "+00:00" in data[0]["created_at"]
    assert "\x1b[" not in result.stdout


def test_handoff_list_respects_limit(handoff_project: Path) -> None:
    """Verify the history limit is honored."""
    _persist_handoff(handoff_project)
    _persist_handoff(handoff_project)

    data = json.loads(runner.invoke(app, ["handoff", "list", "--limit", "1", "--json"]).stdout)
    assert len(data) == 1


# --- show ---


def test_handoff_show_human_output(handoff_project: Path) -> None:
    """Verify show renders the canonical engineering context clearly."""
    record = _persist_handoff(handoff_project)

    result = runner.invoke(app, ["handoff", "show", record.id])
    assert result.exit_code == 0
    output = result.stdout
    assert record.id in output
    assert "CortexShift Handoff Protocol v1" in output
    assert "Original Objective" in output
    assert "Requirements" in output
    assert "Completed" in output
    assert "Remaining" in output
    assert "Git State" in output
    assert "Recommended Next Action" in output
    assert "No structured decisions are recorded" in output


def test_handoff_show_json_contract(handoff_project: Path) -> None:
    """Verify show JSON exposes the full record without rendered prompts."""
    record = _persist_handoff(handoff_project, git_snapshot_id=None)

    result = runner.invoke(app, ["handoff", "show", record.id, "--json"])
    assert result.exit_code == 0

    data = json.loads(result.stdout)
    assert data["id"] == record.id
    assert data["protocol_version"] == 1
    assert data["source_session_id"] == record.source_session_id
    assert data["source_provider_id"] == "claude"
    assert data["target_provider_id"] == "codex"
    assert data["status"] == "prepared"
    assert data["payload"]["task_id"] == record.payload.task_id
    assert data["payload"]["git_state"]["status"] == "ready"

    # Transport representations and provider responses are never exposed.
    assert "rendered_context" not in data
    assert "CORTEXSHIFT HANDOFF PROTOCOL" not in result.stdout
    assert "AUTHORITY ORDER" not in result.stdout
    assert "\x1b[" not in result.stdout


def test_handoff_show_missing_id(handoff_project: Path) -> None:
    """Verify an unknown handoff identifier fails cleanly."""
    result = runner.invoke(app, ["handoff", "show", "handoff_ghost"])
    assert result.exit_code == 1
    output = result.stderr + result.stdout
    assert "handoff_ghost" in output
    assert "was not found" in output
    assert "Traceback" not in output


def _all_keys(node: object) -> set[str]:
    """Collect every JSON field name in a nested structure."""
    collected: set[str] = set()
    if isinstance(node, dict):
        collected |= set(node.keys())
        for value in node.values():
            collected |= _all_keys(value)
    elif isinstance(node, list):
        for item in node:
            collected |= _all_keys(item)
    return collected


def test_handoff_json_carries_no_credential_or_transcript_fields(handoff_project: Path) -> None:
    """Verify no credential, transcript, prompt, or diff field exists in machine output.

    Field names are checked rather than free text, because legitimate canonical task
    content (for example a requirement mentioning refresh tokens) may contain such words.
    """
    record = _persist_handoff(handoff_project)
    data = json.loads(runner.invoke(app, ["handoff", "show", record.id, "--json"]).stdout)

    keys = {key.lower() for key in _all_keys(data)}
    forbidden = {
        "token",
        "tokens",
        "api_key",
        "apikey",
        "secret",
        "password",
        "authorization",
        "credential",
        "credentials",
        "transcript",
        "prompt",
        "rendered_context",
        "rendered_prompt",
        "response",
        "bootstrap_response",
        "diff",
        "remote_url",
        "env",
        "environment",
    }
    assert keys & forbidden == set()
