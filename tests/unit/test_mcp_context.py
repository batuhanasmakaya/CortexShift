"""Unit tests for MCP execution context resolution and validation."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.errors import McpContextError
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task
from cortexshift.mcp.context import (
    ENV_MCP_READ_ONLY,
    ENV_PROJECT_ROOT,
    ENV_PROVIDER_ID,
    ENV_SESSION_ID,
    ENV_TASK_ID,
    resolve_mcp_context,
)


def _seed(
    tmp_path: Path,
) -> tuple[Project, Task, Session, SQLiteStateStore]:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(db_file)
    project = Project(name="MCPTest", repo_path=str(tmp_path))
    store.save_project(project)

    task = Task(project_id=project.id, title="Test Task", objective="Testing MCP")
    store.save_task(task)
    store.set_active_task_id(project.id, task.id)

    session = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
    store.save_session(session)

    return project, task, session, store


def test_resolve_mcp_context_managed_valid(tmp_path: Path) -> None:
    project, task, session, store = _seed(tmp_path)
    env = {
        ENV_PROJECT_ROOT: str(tmp_path),
        ENV_SESSION_ID: session.id,
        ENV_TASK_ID: task.id,
        ENV_PROVIDER_ID: str(PROVIDER_CLAUDE),
    }

    ctx, resolved_store = resolve_mcp_context(
        project_root_override=tmp_path, env_override=env, store_override=store
    )
    assert ctx.managed_session is True
    assert ctx.read_only is False
    assert ctx.can_mutate() is True
    assert ctx.project_id == project.id
    assert ctx.task_id == task.id
    assert ctx.session_id == session.id
    assert ctx.provider_id == session.provider_id
    resolved_store.close()


def test_resolve_mcp_context_unmanaged_fallback(tmp_path: Path) -> None:
    project, task, _, store = _seed(tmp_path)
    env = {
        ENV_PROJECT_ROOT: str(tmp_path),
    }

    ctx, resolved_store = resolve_mcp_context(
        project_root_override=tmp_path, env_override=env, store_override=store
    )
    assert ctx.managed_session is False
    assert ctx.read_only is True
    assert ctx.can_mutate() is False
    assert ctx.project_id == project.id
    assert ctx.task_id == task.id
    assert ctx.session_id is None
    assert ctx.provider_id is None
    resolved_store.close()


def test_resolve_mcp_context_explicit_read_only(tmp_path: Path) -> None:
    project, task, session, store = _seed(tmp_path)
    env = {
        ENV_PROJECT_ROOT: str(tmp_path),
        ENV_SESSION_ID: session.id,
        ENV_TASK_ID: task.id,
        ENV_PROVIDER_ID: str(PROVIDER_CLAUDE),
        ENV_MCP_READ_ONLY: "1",
    }

    ctx, resolved_store = resolve_mcp_context(
        project_root_override=tmp_path, env_override=env, store_override=store
    )
    assert ctx.managed_session is True
    assert ctx.read_only is True
    assert ctx.can_mutate() is False
    resolved_store.close()


def test_resolve_mcp_context_session_not_found(tmp_path: Path) -> None:
    project, task, _, store = _seed(tmp_path)
    env = {
        ENV_PROJECT_ROOT: str(tmp_path),
        ENV_SESSION_ID: "non-existent-session",
        ENV_TASK_ID: task.id,
        ENV_PROVIDER_ID: str(PROVIDER_CLAUDE),
    }

    with pytest.raises(McpContextError, match="Session 'non-existent-session' not found"):
        resolve_mcp_context(project_root_override=tmp_path, env_override=env, store_override=store)
    store.close()


def test_resolve_mcp_context_task_mismatch(tmp_path: Path) -> None:
    project, task, session, store = _seed(tmp_path)
    env = {
        ENV_PROJECT_ROOT: str(tmp_path),
        ENV_SESSION_ID: session.id,
        ENV_TASK_ID: "different-task-id",
        ENV_PROVIDER_ID: str(PROVIDER_CLAUDE),
    }

    with pytest.raises(McpContextError, match="does not match environment task"):
        resolve_mcp_context(project_root_override=tmp_path, env_override=env, store_override=store)
    store.close()


def test_resolve_mcp_context_provider_mismatch(tmp_path: Path) -> None:
    project, task, session, store = _seed(tmp_path)
    env = {
        ENV_PROJECT_ROOT: str(tmp_path),
        ENV_SESSION_ID: session.id,
        ENV_TASK_ID: task.id,
        ENV_PROVIDER_ID: str(PROVIDER_CODEX),
    }

    with pytest.raises(McpContextError, match="does not match environment provider"):
        resolve_mcp_context(project_root_override=tmp_path, env_override=env, store_override=store)
    store.close()


def test_resolve_mcp_context_no_project_found(tmp_path: Path) -> None:
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(McpContextError, match="No initialized CortexShift project found"):
        resolve_mcp_context(project_root_override=empty_dir, env_override={})


def test_resolve_mcp_context_project_root_mismatch(tmp_path: Path) -> None:
    project, task, session, store = _seed(tmp_path)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    (other_dir / ".cortexshift").mkdir()
    (other_dir / ".cortexshift" / "state.sqlite3").touch()
    env = {
        ENV_PROJECT_ROOT: str(other_dir),
    }
    with pytest.raises(McpContextError, match="Project root mismatch"):
        resolve_mcp_context(project_root_override=other_dir, env_override=env, store_override=store)
    store.close()
