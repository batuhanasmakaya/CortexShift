"""End-to-end regression tests for the managed MCP grandchild process.

`cortexshift run <provider>` spawns a provider CLI, and the provider CLI spawns the
CortexShift MCP server. CortexShift never spawns the MCP server itself, so it cannot
assume its own environment reaches it. Codex in particular starts MCP servers with a
sanitized environment plus whatever the per-server configuration declares, which is how a
real managed Codex session ended up read-only with `session: null`.

These tests reproduce that spawn model without a provider CLI and without provider quota:
a hermetic managed launch produces the real per-launch MCP configuration, and the MCP
server is then started with *only* the environment that configuration declares.
"""

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cortexshift.adapters.providers.claude import ClaudeRuntimeAdapter
from cortexshift.adapters.providers.codex import CodexRuntimeAdapter
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.run_service import ProviderRuntimeRegistry, RunService
from cortexshift.domain.mcp_binding import (
    ENV_MCP_READ_ONLY,
    ENV_PROJECT_ROOT,
    ENV_PROVIDER_ID,
    ENV_SESSION_ID,
    ENV_TASK_ID,
)
from cortexshift.domain.project import Project
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task
from cortexshift.ports.process_runner import InteractiveProcessRunner

READ_TOOLS = {
    "get_project_context",
    "get_current_task",
    "get_latest_checkpoint",
    "get_repository_status",
}
WRITE_TOOLS = {
    "set_current_work",
    "mark_completed",
    "add_remaining",
    "record_issue",
    "record_decision",
    "create_checkpoint",
}

# What a provider is willing to hand to an MCP server it spawns is the provider's choice,
# not CortexShift's. Codex forwards a small fixed allowlist plus the server's declared
# `env` table; modelling that here keeps the test honest, because nothing CortexShift-
# specific can leak in implicitly.
_INHERITABLE = (
    "PATH",
    "HOME",
    "LOGNAME",
    "SHELL",
    "USER",
    "TMPDIR",
    "LANG",
    "APPDATA",
    "COMSPEC",
    "LOCALAPPDATA",
    "PATHEXT",
    "PROGRAMFILES",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USERNAME",
    "USERPROFILE",
    "WINDIR",
)


class RecordingProcessRunner(InteractiveProcessRunner):
    """Captures the argv a provider CLI would have been spawned with."""

    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []

    def run_interactive(
        self, argv: list[str], cwd: Path | str, env: dict[str, str] | None = None
    ) -> int:
        self.invocations.append({"argv": list(argv), "cwd": cwd, "env": dict(env or {})})
        return 0


def _seed(root: Path) -> tuple[Project, Task]:
    db_file = root / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(db_file) as store:
        project = Project(name=f"ManagedMcpRepo-{root.name}", repo_path=str(root))
        store.save_project(project)
        task = Task(
            project_id=project.id,
            title="Managed MCP Task",
            objective="Expose write tools inside a managed provider session",
            remaining_items=["Bind the managed session"],
        )
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)
    return project, task


def _run_managed(root: Path, provider: str) -> tuple[Session, list[str]]:
    """Perform a hermetic managed launch and return the session plus the provider argv."""
    runner = RecordingProcessRunner()
    service = RunService(
        registry=ProviderRuntimeRegistry([ClaudeRuntimeAdapter(), CodexRuntimeAdapter()]),
        process_runner=runner,
        which_fn=lambda cmd: "/fake/" + cmd,
        is_tty_fn=lambda: True,
    )
    session = service.run(provider, start_dir=root)
    return session, runner.invocations[0]["argv"]


def _declared_mcp_env(provider: str, argv: list[str]) -> dict[str, str]:
    """Extract the environment a provider would apply to the MCP server it spawns."""
    if provider == "claude":
        server = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]["cortexshift"]
    else:
        overrides = [argv[i + 1] for i, arg in enumerate(argv) if arg == "-c"]
        server = tomllib.loads("\n".join(overrides))["mcp_servers"]["cortexshift"]
    assert server["command"] == sys.executable
    assert server["args"] == ["-m", "cortexshift", "mcp", "serve"]
    return cast(dict[str, str], server.get("env", {}))


def _sanitized_env(declared: dict[str, str]) -> dict[str, str]:
    """Build the environment a provider-spawned MCP server would actually receive."""
    env = {name: os.environ[name] for name in _INHERITABLE if name in os.environ}
    env.update(declared)
    return env


def _server_params(env: dict[str, str], cwd: Path) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "cortexshift", "mcp", "serve"],
        env=env,
        cwd=str(cwd),
    )


def _serve_outcome(env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Start the MCP server with a closed stdin and report how it terminated."""
    return subprocess.run(
        [sys.executable, "-m", "cortexshift", "mcp", "serve"],
        cwd=str(cwd),
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=60,
    )


# ---------------------------------------------------------------------------
# C + E. A managed launch exposes write tools and binds checkpoints to the session
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("provider", ["codex", "claude"])
async def test_managed_launch_exposes_write_tools_to_the_mcp_grandchild(
    tmp_path: Path, provider: str
) -> None:
    _, task = _seed(tmp_path)
    session, argv = _run_managed(tmp_path, provider)

    declared = _declared_mcp_env(provider, argv)
    assert declared[ENV_SESSION_ID] == session.id
    assert declared[ENV_TASK_ID] == task.id
    assert declared[ENV_PROVIDER_ID] == provider
    assert declared[ENV_PROJECT_ROOT] == str(tmp_path)
    assert declared[ENV_MCP_READ_ONLY] == "0"

    async with (
        stdio_client(_server_params(_sanitized_env(declared), tmp_path)) as (read, write),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        assert {t.name for t in (await client.list_tools()).tools} == READ_TOOLS | WRITE_TOOLS

        context = json.loads(
            cast(Any, (await client.call_tool("get_project_context")).content[0]).text
        )
        assert context["session"] is not None
        assert context["session"]["id"] == session.id
        assert context["session"]["provider_id"] == provider
        assert context["task"]["id"] == task.id

        created = await client.call_tool(
            "create_checkpoint",
            {"decisions": ["Bound the managed session"], "note": "Managed milestone"},
        )
        assert not created.is_error
        checkpoint_id = json.loads(cast(Any, created.content[0]).text)["checkpoint_id"]

    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3") as store:
        persisted = store.get_checkpoint(checkpoint_id)
        assert persisted is not None
        assert persisted.session_id == session.id
        assert persisted.task_id == task.id
        source = persisted.payload.source_session
        assert source is not None
        assert source.session_id == session.id
        assert str(source.provider_id) == provider


# ---------------------------------------------------------------------------
# D + F. Without a trusted binding the server stays read-only and cannot be escalated
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unmanaged_grandchild_exposes_only_read_tools(tmp_path: Path) -> None:
    _, task = _seed(tmp_path)

    async with (
        stdio_client(_server_params(_sanitized_env({}), tmp_path)) as (read, write),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        assert {t.name for t in (await client.list_tools()).tools} == READ_TOOLS

        context = json.loads(
            cast(Any, (await client.call_tool("get_project_context")).content[0]).text
        )
        assert context["session"] is None
        # Read tools still resolve the active task, which is what the real Codex run saw.
        assert context["task"]["id"] == task.id

        # F. No tool argument can promote this server into managed mode.
        forged = {
            "session_id": "sess_forged",
            "task_id": task.id,
            "project_id": "proj_forged",
            "project_root": str(tmp_path),
            "managed_session": True,
            "read_only": False,
            "current_work": "escalation attempt",
            "items": ["escalation attempt"],
            "decision": "escalation attempt",
        }
        for tool in sorted(WRITE_TOOLS):
            result = await client.call_tool(tool, forged)
            assert result.is_error, f"{tool} must not be callable in unmanaged mode"

        assert {t.name for t in (await client.list_tools()).tools} == READ_TOOLS
        after = json.loads(
            cast(Any, (await client.call_tool("get_project_context")).content[0]).text
        )
        assert after["session"] is None

    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3") as store:
        assert store.get_latest_checkpoint(task.id) is None
        unchanged = store.get_task(task.id)
        assert unchanged is not None
        assert unchanged.current_work is None
        assert unchanged.remaining == ["Bind the managed session"]


@pytest.mark.anyio
async def test_read_only_managed_launch_keeps_write_tools_unregistered(tmp_path: Path) -> None:
    """A managed binding marked read-only exposes exactly the unmanaged tool surface."""
    _seed(tmp_path)
    _, argv = _run_managed(tmp_path, "codex")
    declared = dict(_declared_mcp_env("codex", argv))
    declared[ENV_MCP_READ_ONLY] = "1"

    async with (
        stdio_client(_server_params(_sanitized_env(declared), tmp_path)) as (read, write),
        ClientSession(read, write) as client,
    ):
        await client.initialize()
        assert {t.name for t in (await client.list_tools()).tools} == READ_TOOLS


# ---------------------------------------------------------------------------
# G. Stale, mismatched, or replayed bindings fail closed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param(ENV_SESSION_ID, "sess_deleted", id="unknown-session"),
        pytest.param(ENV_TASK_ID, "task_other", id="task-mismatch"),
        pytest.param(ENV_PROVIDER_ID, "claude", id="provider-mismatch"),
    ],
)
def test_invalid_binding_refuses_to_serve(tmp_path: Path, key: str, value: str) -> None:
    _seed(tmp_path)
    _, argv = _run_managed(tmp_path, "codex")
    declared = dict(_declared_mcp_env("codex", argv))
    declared[key] = value

    result = _serve_outcome(_sanitized_env(declared), tmp_path)

    assert result.returncode != 0
    assert "MCP server failed to start" in result.stderr
    assert result.stdout == ""


def test_binding_is_not_replayable_against_another_project(tmp_path: Path) -> None:
    """A binding minted for one project must not grant write access in another."""
    project_a = tmp_path / "a"
    project_b = tmp_path / "b"
    project_a.mkdir()
    project_b.mkdir()
    _seed(project_a)
    _seed(project_b)

    session, argv = _run_managed(project_a, "codex")
    declared = dict(_declared_mcp_env("codex", argv))
    assert declared[ENV_SESSION_ID] == session.id
    declared[ENV_PROJECT_ROOT] = str(project_b)

    result = _serve_outcome(_sanitized_env(declared), project_b)

    assert result.returncode != 0
    assert "MCP server failed to start" in result.stderr
    assert result.stdout == ""


def test_managed_binding_still_serves_when_valid(tmp_path: Path) -> None:
    """Fail-closed checks must not be satisfied by a server that never starts at all."""
    _seed(tmp_path)
    _, argv = _run_managed(tmp_path, "codex")

    result = _serve_outcome(_sanitized_env(_declared_mcp_env("codex", argv)), tmp_path)

    assert result.returncode == 0
    assert "MCP server failed to start" not in result.stderr


# ---------------------------------------------------------------------------
# Guard rail: the test's own model of provider spawning must stay honest
# ---------------------------------------------------------------------------


def test_sanitized_environment_carries_no_cortexshift_context() -> None:
    """The simulated provider environment must never leak CortexShift context implicitly."""
    assert [name for name in _sanitized_env({}) if name.startswith("CORTEXSHIFT_")] == []
