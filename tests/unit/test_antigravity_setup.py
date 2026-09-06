"""Unit tests for Antigravity MCP configuration and CLI setup command."""

import json
from pathlib import Path

import pytest

from cortexshift.adapters.providers.antigravity import (
    ANTIGRAVITY_MCP_CONFIG_REL_PATH,
    CORTEXSHIFT_ANTIGRAVITY_MCP_SERVER,
    is_antigravity_mcp_configured,
    setup_antigravity_mcp,
)
from cortexshift.cli.app import app
from cortexshift.domain.errors import CortexShiftError
from cortexshift.domain.project import Project
from tests.cli_runner import AnsiFreeCliRunner

runner = AnsiFreeCliRunner()


def test_setup_creates_new_config(tmp_path: Path) -> None:
    res = setup_antigravity_mcp(tmp_path)
    assert res["action"] == "created"
    assert res["changed"] is True
    assert res["dry_run"] is False

    target_file = tmp_path / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    assert target_file.is_file()

    data = json.loads(target_file.read_text(encoding="utf-8"))
    assert "mcpServers" in data
    assert data["mcpServers"]["cortexshift"] == CORTEXSHIFT_ANTIGRAVITY_MCP_SERVER
    assert is_antigravity_mcp_configured(tmp_path) is True


def test_setup_dry_run_does_not_write(tmp_path: Path) -> None:
    res = setup_antigravity_mcp(tmp_path, dry_run=True)
    assert res["action"] == "created"
    assert res["changed"] is True
    assert res["dry_run"] is True

    target_file = tmp_path / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    assert not target_file.exists()
    assert is_antigravity_mcp_configured(tmp_path) is False


def test_setup_noop_if_already_configured(tmp_path: Path) -> None:
    setup_antigravity_mcp(tmp_path)
    # Second run without changes
    res = setup_antigravity_mcp(tmp_path)
    assert res["action"] == "noop"
    assert res["changed"] is False
    assert is_antigravity_mcp_configured(tmp_path) is True


def test_setup_merges_preserving_existing_servers(tmp_path: Path) -> None:
    target_file = tmp_path / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    target_file.parent.mkdir(parents=True, exist_ok=True)
    initial_config = {
        "mcpServers": {
            "custom-tool": {
                "command": "custom",
                "args": ["serve"],
            }
        }
    }
    target_file.write_text(json.dumps(initial_config), encoding="utf-8")

    res = setup_antigravity_mcp(tmp_path)
    assert res["action"] == "updated"
    assert res["changed"] is True

    data = json.loads(target_file.read_text(encoding="utf-8"))
    assert "custom-tool" in data["mcpServers"]
    assert "cortexshift" in data["mcpServers"]
    assert data["mcpServers"]["custom-tool"]["command"] == "custom"
    assert data["mcpServers"]["cortexshift"] == CORTEXSHIFT_ANTIGRAVITY_MCP_SERVER


def test_setup_conflict_without_force_raises(tmp_path: Path) -> None:
    target_file = tmp_path / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    target_file.parent.mkdir(parents=True, exist_ok=True)
    conflicting_config = {
        "mcpServers": {
            "cortexshift": {
                "command": "different-tool",
                "args": ["foo"],
            }
        }
    }
    target_file.write_text(json.dumps(conflicting_config), encoding="utf-8")

    with pytest.raises(CortexShiftError, match="Conflicting configuration"):
        setup_antigravity_mcp(tmp_path, force=False)


def test_setup_conflict_with_force_overwrites(tmp_path: Path) -> None:
    target_file = tmp_path / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    target_file.parent.mkdir(parents=True, exist_ok=True)
    conflicting_config = {
        "mcpServers": {
            "cortexshift": {
                "command": "different-tool",
                "args": ["foo"],
            },
            "other": {"command": "other"},
        }
    }
    target_file.write_text(json.dumps(conflicting_config), encoding="utf-8")

    res = setup_antigravity_mcp(tmp_path, force=True)
    assert res["action"] == "updated"
    assert res["changed"] is True

    data = json.loads(target_file.read_text(encoding="utf-8"))
    assert data["mcpServers"]["cortexshift"] == CORTEXSHIFT_ANTIGRAVITY_MCP_SERVER
    assert data["mcpServers"]["other"] == {"command": "other"}


def test_setup_invalid_json_raises(tmp_path: Path) -> None:
    target_file = tmp_path / ANTIGRAVITY_MCP_CONFIG_REL_PATH
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text("NOT_JSON", encoding="utf-8")

    with pytest.raises(CortexShiftError, match="invalid JSON"):
        setup_antigravity_mcp(tmp_path)


def test_cli_mcp_setup_antigravity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cortexshift.adapters.sqlite.store import SQLiteStateStore

    monkeypatch.chdir(tmp_path)
    # Initialize a valid cortexshift repository
    store = SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3")
    store.save_project(Project(name="TestProj", repo_path=str(tmp_path)))
    store.close()

    # Dry run
    res = runner.invoke(app, ["mcp", "setup", "antigravity", "--dry-run"])
    assert res.exit_code == 0
    assert "Dry Run" in res.stdout
    assert "True" in res.stdout
    assert not (tmp_path / ANTIGRAVITY_MCP_CONFIG_REL_PATH).exists()

    # Real run
    res = runner.invoke(app, ["mcp", "setup", "antigravity"])
    assert res.exit_code == 0
    assert (tmp_path / ANTIGRAVITY_MCP_CONFIG_REL_PATH).is_file()

    # Second run (noop)
    res = runner.invoke(app, ["mcp", "setup", "antigravity"])
    assert res.exit_code == 0
    assert "noop" in res.stdout or "already up to date" in res.stdout


def test_cli_mcp_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fixed_console_width: int
) -> None:
    from cortexshift.adapters.sqlite.store import SQLiteStateStore

    monkeypatch.chdir(tmp_path)
    store = SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3")
    store.save_project(Project(name="StatusProj", repo_path=str(tmp_path)))
    store.close()

    # Human-readable output
    res = runner.invoke(app, ["mcp", "status"])
    assert res.exit_code == 0
    assert "CortexShift MCP Status" in res.stdout
    assert "Claude Code" in res.stdout
    assert "OpenAI Codex" in res.stdout
    assert "Google Antigravity" in res.stdout

    # JSON output
    res_json = runner.invoke(app, ["mcp", "status", "--json"])
    assert res_json.exit_code == 0
    data = json.loads(res_json.stdout)
    assert data["mcp_sdk"]["available"] is True
    assert data["server"]["name"] == "cortexshift"
    assert "get_project_context" in data["server"]["read_tools"]
    assert "set_current_work" in data["server"]["write_tools"]
    assert data["project"]["name"] == "StatusProj"
