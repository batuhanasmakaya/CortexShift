"""Integration tests for CortexShift MCP stdio server wire protocol."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from cortexshift.adapters.providers.claude import CLAUDE_HANDOFF_PROMPT
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.switch_service import SwitchService
from cortexshift.domain.checkpoint import CheckpointTestProvenance
from cortexshift.domain.handoff import MAX_HANDOFF_BYTES, HandoffStatus
from cortexshift.domain.mcp_binding import McpSessionBinding
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task
from tests.factories import seed_project, seed_session
from tests.mcp_invocations import LiveMcpInvocations


def _init_repo(tmp_path: Path) -> tuple[Project, Task, Session]:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(db_file)

    proj = Project(name="StdioRepo", repo_path=str(tmp_path))
    store.save_project(proj)

    task = Task(
        project_id=proj.id,
        title="Stdio Task",
        objective="Verify MCP wire protocol",
        completed_items=["Initial setup"],
        remaining_items=["Write tests"],
    )
    store.save_task(task)
    store.set_active_task_id(proj.id, task.id)

    sess = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.RUNNING)
    store.save_session(sess)
    store.close()
    return proj, task, sess


@pytest.mark.anyio
async def test_mcp_stdio_server_lifecycle_and_tools(
    tmp_path: Path, live_mcp: LiveMcpInvocations
) -> None:
    proj, task, sess = _init_repo(tmp_path)
    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3") as store:
        token = live_mcp.open(tmp_path, store, sess)
    binding = McpSessionBinding.from_session(sess, tmp_path)
    binding = binding.model_copy(update={"invocation_token": token})

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cortexshift", "mcp", "serve"],
        env=binding.to_env(),
    )

    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        # 1. List tools: managed session gets 11 tools
        tools_result = await client.list_tools()
        tool_names = {t.name for t in tools_result.tools}
        expected = {
            "get_project_context",
            "get_current_task",
            "get_handoff_context",
            "get_latest_checkpoint",
            "get_repository_status",
            "set_current_work",
            "mark_completed",
            "add_remaining",
            "record_issue",
            "record_decision",
            "create_checkpoint",
        }
        assert tool_names == expected

        # 2. Call get_project_context
        ctx_res = await client.call_tool("get_project_context")
        assert not ctx_res.is_error
        text = cast(Any, ctx_res.content[0]).text
        data = json.loads(text)
        assert data["project"]["name"] == "StdioRepo"
        assert data["task"]["id"] == task.id

        # 3. Call set_current_work
        work_res = await client.call_tool(
            "set_current_work",
            {"current_work": "Active integration testing"},
        )
        assert not work_res.is_error

        # 4. Call add_remaining
        add_res = await client.call_tool(
            "add_remaining",
            {"items": ["Document protocol", "Verify benchmarks"]},
        )
        assert not add_res.is_error

        # 5. Call mark_completed
        done_res = await client.call_tool(
            "mark_completed",
            {"items": ["Write tests", "Document protocol"]},
        )
        assert not done_res.is_error

        # 6. Call record_decision
        dec_res = await client.call_tool(
            "record_decision",
            {"decision": "Standardized on local stdio MCP transport"},
        )
        assert not dec_res.is_error
        dec_data = json.loads(cast(Any, dec_res.content[0]).text)
        assert dec_data["checkpoint_id"].startswith("cp_")

        # 7. Call create_checkpoint
        cp_res = await client.call_tool(
            "create_checkpoint",
            {
                "decisions": ["Secondary architectural decision"],
                "test_summary": "42 passed in 1.2s",
                "note": "Milestone before handoff",
            },
        )
        assert not cp_res.is_error
        cp_data = json.loads(cast(Any, cp_res.content[0]).text)
        assert cp_data["checkpoint_id"].startswith("cp_")
        assert cp_data["test_summary"] == "42 passed in 1.2s"
        assert cp_data["test_provenance"] == "reported"

    # Verify mutations are durably persisted in SQLiteStateStore
    store = SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3")
    updated_task = store.get_task(task.id)
    assert updated_task is not None
    assert updated_task.current_work == "Active integration testing"
    assert "Write tests" in updated_task.completed
    assert "Document protocol" in updated_task.completed
    assert "Write tests" not in updated_task.remaining
    assert "Verify benchmarks" in updated_task.remaining

    latest_cp = store.get_latest_checkpoint(task.id)
    assert latest_cp is not None
    assert "Secondary architectural decision" in latest_cp.payload.decisions
    assert latest_cp.payload.test_status.summary == "42 passed in 1.2s"
    assert latest_cp.payload.test_status.provenance == CheckpointTestProvenance.REPORTED
    store.close()


@pytest.mark.anyio
async def test_mcp_stdio_read_only_mode(tmp_path: Path) -> None:
    proj, task, sess = _init_repo(tmp_path)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cortexshift", "mcp", "serve"],
        env={
            "CORTEXSHIFT_PROJECT_ROOT": str(tmp_path),
            "CORTEXSHIFT_TASK_ID": task.id,
            "CORTEXSHIFT_SESSION_ID": sess.id,
            "CORTEXSHIFT_PROVIDER_ID": "claude",
            "CORTEXSHIFT_MCP_READ_ONLY": "1",
        },
    )

    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        # Read-only session should expose only 4 read tools
        tools_result = await client.list_tools()
        tool_names = {t.name for t in tools_result.tools}
        expected = {
            "get_project_context",
            "get_current_task",
            "get_latest_checkpoint",
            "get_repository_status",
        }
        assert tool_names == expected

        # Attempting to call an unregistered/write tool should fail
        call_res = await client.call_tool(
            "set_current_work",
            {"current_work": "Should fail"},
        )
        assert call_res.is_error


def test_claude_switch_serves_only_its_bound_handoff(tmp_path: Path) -> None:
    """Exercise the actual MCP wire and the native interactive adapter without a provider."""
    objective = "Continue the implementation.\n" + "詳細 🎉 " * 3500 + "\nKeep this instruction."
    project, task = seed_project(tmp_path, objective=objective, constraints=["Keep schema v6"])
    source = seed_session(tmp_path, task.id, provider_id=PROVIDER_CODEX)

    async def fetch(config: dict[str, Any], expect_live: bool) -> str | None:
        params = StdioServerParameters(
            command=config["command"], args=config["args"], env=config["env"], cwd=tmp_path
        )
        async with (
            stdio_client(params) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            names = {item.name for item in (await client.list_tools()).tools}
            assert ("get_handoff_context" in names) == expect_live
            if not expect_live:
                return None
            response = await client.call_tool("get_handoff_context")
            assert not response.is_error
            (content,) = response.content
            assert isinstance(content, TextContent)
            return content.text

    class McpReader:
        config: dict[str, Any]
        context: str

        def run_interactive(
            self, argv: list[str], cwd: Path | str, env: dict[str, str] | None = None
        ) -> int:
            assert argv[-1] == CLAUDE_HANDOFF_PROMPT
            assert all("Keep this instruction" not in arg for arg in argv)
            assert cwd == tmp_path.resolve()
            self.config = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"][
                "cortexshift"
            ]

            with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3") as store:
                (before,) = store.list_handoffs()
                assert before.status == HandoffStatus.PREPARED
                assert before.delivered_at is None
                assert before.target_session_id is not None
                another = Task(project_id=project.id, title="Other", objective="Unrelated data")
                store.save_task(another)
                store.set_active_task_id(project.id, another.id)

                context = asyncio.run(fetch(self.config, expect_live=True))
                assert context is not None
                self.context = context
                delivered = store.get_handoff(before.id)
                assert delivered is not None and delivered.delivered_at is not None
                assert asyncio.run(fetch(self.config, expect_live=True)) == context
                assert store.get_handoff(before.id) == delivered
            return 0

    provider = McpReader()
    prepared: list[str] = []
    result = SwitchService(
        process_runner=provider,
        which_fn=lambda _: "fake claude",
        is_tty_fn=lambda: True,
    ).switch(
        "claude",
        start_dir=tmp_path,
        on_prepared=lambda _, rendered: prepared.append(rendered.text),
    )

    assert result.handoff.status == HandoffStatus.DELIVERED
    assert result.handoff.source_session_id == source.id
    assert result.target_session.status == SessionStatus.COMPLETED
    assert prepared == [provider.context]
    assert objective in provider.context
    assert "Keep schema v6" in provider.context
    assert "Unrelated data" not in provider.context
    assert len(provider.context.encode("utf-8")) <= MAX_HANDOFF_BYTES
    assert asyncio.run(fetch(provider.config, expect_live=False)) is None
    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3") as store:
        assert store.get_task(task.id) == task
        assert store.get_session(source.id) == source
