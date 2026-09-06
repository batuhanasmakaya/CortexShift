"""Integration tests verifying MCP execution context binding and task isolation."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.errors import (
    McpReadOnlyError,
    TaskNotFoundError,
)
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade


def test_mcp_context_isolates_bound_task_even_if_active_task_changes(tmp_path: Path) -> None:
    """Invariant: MCP tools mutate only the Task bound at launch, not whatever task is active."""
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(db_file)

    proj = Project(name="IsolationRepo", repo_path=str(tmp_path))
    store.save_project(proj)

    # Task A: initially active
    task_a = Task(
        project_id=proj.id,
        title="Task A",
        objective="Work on Task A",
        current_work="A initial work",
        completed_items=["A1"],
        remaining_items=["A2"],
    )
    store.save_task(task_a)
    store.set_active_task_id(proj.id, task_a.id)

    # Task B: later made active
    task_b = Task(
        project_id=proj.id,
        title="Task B",
        objective="Work on Task B",
        current_work="B initial work",
        completed_items=["B1"],
        remaining_items=["B2"],
    )
    store.save_task(task_b)

    sess_a = Session(task_id=task_a.id, provider_id=PROVIDER_CLAUDE)
    store.save_session(sess_a)

    # Launch context bound specifically to Task A
    ctx_a = McpExecutionContext(
        project_root=tmp_path,
        project_id=proj.id,
        task_id=task_a.id,
        session_id=sess_a.id,
        provider_id=sess_a.provider_id,
        managed_session=True,
        read_only=False,
    )
    facade_a = McpApplicationFacade(ctx_a, store)

    # Now change active task in DB to Task B
    store.set_active_task_id(proj.id, task_b.id)
    assert store.get_active_task_id(proj.id) == task_b.id

    # Mutate via Task A's MCP facade
    facade_a.set_current_work("Mutated by Task A's MCP session")
    facade_a.mark_completed(["A2"])
    facade_a.add_remaining(["A3"])

    # Verify Task A was updated
    updated_a = store.get_task(task_a.id)
    assert updated_a is not None
    assert updated_a.current_work == "Mutated by Task A's MCP session"
    assert "A2" in updated_a.completed
    assert "A2" not in updated_a.remaining
    assert "A3" in updated_a.remaining

    # Verify Task B was completely untouched
    untouched_b = store.get_task(task_b.id)
    assert untouched_b is not None
    assert untouched_b.current_work == "B initial work"
    assert untouched_b.completed == ["B1"]
    assert untouched_b.remaining == ["B2"]
    store.close()


def test_mcp_unmanaged_context_cannot_mutate(tmp_path: Path) -> None:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(db_file)

    proj = Project(name="UnmanagedRepo", repo_path=str(tmp_path))
    store.save_project(proj)

    task = Task(project_id=proj.id, title="Unmanaged Task", objective="Objective")
    store.save_task(task)
    store.set_active_task_id(proj.id, task.id)

    # Context without managed session
    ctx = McpExecutionContext(
        project_root=tmp_path,
        project_id=proj.id,
        task_id=task.id,
        managed_session=False,
        read_only=True,
    )
    facade = McpApplicationFacade(ctx, store)

    with pytest.raises(McpReadOnlyError):
        facade.set_current_work("Attempted mutation")

    with pytest.raises(McpReadOnlyError):
        facade.mark_completed(["Item"])

    store.close()


def test_mcp_context_with_nonexistent_task_fails_safely(tmp_path: Path) -> None:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(db_file)

    proj = Project(name="MissingTaskRepo", repo_path=str(tmp_path))
    store.save_project(proj)

    ctx = McpExecutionContext(
        project_root=tmp_path,
        project_id=proj.id,
        task_id="task_nonexistent_999",
        session_id="sess_123",
        provider_id=PROVIDER_CLAUDE,
        managed_session=True,
        read_only=False,
    )
    facade = McpApplicationFacade(ctx, store)

    with pytest.raises(TaskNotFoundError):
        facade.get_current_task()

    with pytest.raises(TaskNotFoundError):
        facade.set_current_work("Work")

    store.close()
