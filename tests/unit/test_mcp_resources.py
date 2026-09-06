"""Unit tests for CortexShift MCP resources."""

import json
from pathlib import Path
from typing import Any, cast

import pytest
from mcp.server.mcpserver.exceptions import ResourceError

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade
from cortexshift.mcp.server import create_mcp_server


def _setup_test_env(
    tmp_path: Path,
) -> tuple[McpExecutionContext, SQLiteStateStore, Task, Session]:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(db_file)
    project = Project(name="ResourceTest", repo_path=str(tmp_path))
    store.save_project(project)

    task = Task(
        project_id=project.id,
        title="Resource Task",
        objective="Verify MCP resources",
        current_work="Testing resources",
        completed_items=["Wiring"],
        remaining_items=["Testing"],
    )
    store.save_task(task)
    store.set_active_task_id(project.id, task.id)

    session = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
    store.save_session(session)

    ctx = McpExecutionContext(
        project_root=tmp_path,
        project_id=project.id,
        task_id=task.id,
        session_id=session.id,
        provider_id=session.provider_id,
        managed_session=True,
        read_only=False,
    )
    return ctx, store, task, session


@pytest.mark.anyio
async def test_list_resources(tmp_path: Path) -> None:
    ctx, store, _, _ = _setup_test_env(tmp_path)
    server = create_mcp_server(ctx, store)

    resources = await server.list_resources()
    uris = [str(r.uri) for r in resources]

    assert "cortexshift://project" in uris
    assert "cortexshift://task" in uris
    assert "cortexshift://checkpoint/latest" in uris
    assert "cortexshift://repository" in uris
    store.close()


@pytest.mark.anyio
async def test_read_project_resource(tmp_path: Path) -> None:
    ctx, store, task, session = _setup_test_env(tmp_path)
    server = create_mcp_server(ctx, store)

    contents = list(await server.read_resource("cortexshift://project"))
    assert len(contents) == 1
    res0 = cast(Any, contents[0])
    assert res0.mime_type == "application/json"
    data = json.loads(res0.content)

    assert data["project"]["name"] == "ResourceTest"
    assert data["task"]["id"] == task.id
    assert data["session"]["id"] == session.id
    assert "truth_hierarchy" in data
    store.close()


@pytest.mark.anyio
async def test_read_task_resource(tmp_path: Path) -> None:
    ctx, store, task, _ = _setup_test_env(tmp_path)
    server = create_mcp_server(ctx, store)

    contents = list(await server.read_resource("cortexshift://task"))
    assert len(contents) == 1
    res0 = cast(Any, contents[0])
    assert res0.mime_type == "application/json"
    data = json.loads(res0.content)

    assert data["id"] == task.id
    assert data["title"] == "Resource Task"
    assert data["completed_items"] == ["Wiring"]
    assert data["remaining_items"] == ["Testing"]
    store.close()


@pytest.mark.anyio
async def test_read_checkpoint_resource(tmp_path: Path) -> None:
    ctx, store, _, _ = _setup_test_env(tmp_path)
    facade = McpApplicationFacade(ctx, store)
    cp_res = facade.create_checkpoint(
        decisions=["Use stdio transport"],
        test_summary="5 passed",
        note="Resource test checkpoint",
    )

    server = create_mcp_server(ctx, store)
    contents = list(await server.read_resource("cortexshift://checkpoint/latest"))
    assert len(contents) == 1
    res0 = cast(Any, contents[0])
    assert res0.mime_type == "application/json"
    data = json.loads(res0.content)

    assert data["checkpoint"] is not None
    assert data["checkpoint"]["id"] == cp_res.checkpoint_id
    assert data["checkpoint"]["payload"]["decisions"] == ["Use stdio transport"]
    store.close()


@pytest.mark.anyio
async def test_read_checkpoint_resource_empty(tmp_path: Path) -> None:
    ctx, store, _, _ = _setup_test_env(tmp_path)
    server = create_mcp_server(ctx, store)

    contents = list(await server.read_resource("cortexshift://checkpoint/latest"))
    assert len(contents) == 1
    res0 = cast(Any, contents[0])
    assert res0.mime_type == "application/json"
    data = json.loads(res0.content)

    assert data["checkpoint"] is None
    store.close()


@pytest.mark.anyio
async def test_read_repository_resource(tmp_path: Path) -> None:
    ctx, store, _, _ = _setup_test_env(tmp_path)
    server = create_mcp_server(ctx, store)

    contents = list(await server.read_resource("cortexshift://repository"))
    assert len(contents) == 1
    res0 = cast(Any, contents[0])
    assert res0.mime_type == "application/json"
    data = json.loads(res0.content)

    assert "available" in data
    assert "status" in data
    store.close()


@pytest.mark.anyio
async def test_read_unknown_resource_raises(tmp_path: Path) -> None:
    ctx, store, _, _ = _setup_test_env(tmp_path)
    server = create_mcp_server(ctx, store)

    with pytest.raises(ResourceError):
        await server.read_resource("cortexshift://nonexistent")
    store.close()
