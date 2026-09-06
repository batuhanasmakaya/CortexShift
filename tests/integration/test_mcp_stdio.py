"""Integration tests for CortexShift MCP stdio server wire protocol."""

import json
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.checkpoint import CheckpointTestProvenance
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task


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

    sess = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
    store.save_session(sess)
    store.close()
    return proj, task, sess


@pytest.mark.anyio
async def test_mcp_stdio_server_lifecycle_and_tools(tmp_path: Path) -> None:
    proj, task, sess = _init_repo(tmp_path)

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "cortexshift", "mcp", "serve"],
        env={
            "CORTEXSHIFT_PROJECT_ROOT": str(tmp_path),
            "CORTEXSHIFT_TASK_ID": task.id,
            "CORTEXSHIFT_SESSION_ID": sess.id,
            "CORTEXSHIFT_PROVIDER_ID": "claude",
            "CORTEXSHIFT_MCP_READ_ONLY": "0",
        },
    )

    async with (
        stdio_client(server_params) as (read, write),
        ClientSession(read, write) as client,
    ):
        await client.initialize()

        # 1. List tools: managed session gets 10 tools
        tools_result = await client.list_tools()
        tool_names = {t.name for t in tools_result.tools}
        expected = {
            "get_project_context",
            "get_current_task",
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
