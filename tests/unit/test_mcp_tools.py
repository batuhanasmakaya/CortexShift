"""Unit tests for MCP read and write tools."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.checkpoint import (
    CheckpointTestProvenance,
)
from cortexshift.domain.errors import McpReadOnlyError
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade
from cortexshift.mcp.models import (
    MAX_CURRENT_WORK_CHARS,
    MAX_DECISION_CHARS,
    MAX_ITEM_CHARS,
    MAX_ITEMS_PER_CALL,
)


def _setup_test_env(
    tmp_path: Path, managed: bool = True, read_only: bool = False
) -> tuple[McpApplicationFacade, SQLiteStateStore, Task, Session]:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(db_file)
    project = Project(name="ToolTest", repo_path=str(tmp_path))
    store.save_project(project)

    task = Task(
        project_id=project.id,
        title="Implementation Task",
        objective="Test MCP tools thoroughly",
        current_work="Writing test suite",
        completed_items=["Setup schema", "Initial wiring"],
        remaining_items=["Write tests", "Verify coverage"],
        known_issues=["Minor mock issue"],
    )
    store.save_task(task)
    store.set_active_task_id(project.id, task.id)

    session = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
    store.save_session(session)

    ctx = McpExecutionContext(
        project_root=tmp_path,
        project_id=project.id,
        task_id=task.id,
        session_id=session.id if managed else None,
        provider_id=session.provider_id if managed else None,
        managed_session=managed,
        read_only=read_only,
    )

    facade = McpApplicationFacade(ctx, store)
    return facade, store, task, session


def test_get_project_context(tmp_path: Path) -> None:
    facade, store, task, session = _setup_test_env(tmp_path)
    res = facade.get_project_context()
    assert res.project.name == "ToolTest"
    assert res.task is not None
    assert res.task.id == task.id
    assert res.task.title == "Implementation Task"
    assert res.task.completed_count == 2
    assert res.task.remaining_count == 2
    assert res.session is not None
    assert res.session.id == session.id
    assert len(res.truth_hierarchy) == 5
    store.close()


def test_get_current_task(tmp_path: Path) -> None:
    facade, store, task, _ = _setup_test_env(tmp_path)
    res = facade.get_current_task()
    assert res["id"] == task.id
    assert res["title"] == "Implementation Task"
    assert res["current_work"] == "Writing test suite"
    assert res["completed"] == ["Setup schema", "Initial wiring"]
    assert res["remaining"] == ["Write tests", "Verify coverage"]
    assert res["known_issues"] == ["Minor mock issue"]
    store.close()


def test_get_latest_checkpoint(tmp_path: Path) -> None:
    facade, store, task, session = _setup_test_env(tmp_path)
    # Initially no checkpoint
    assert facade.get_latest_checkpoint().checkpoint is None

    # Save a checkpoint via facade
    cp_res = facade.create_checkpoint(
        decisions=["Use stdio transport for MCP"],
        test_summary="8 passed",
        note="Initial milestone",
    )

    res = facade.get_latest_checkpoint()
    assert res.checkpoint is not None
    assert res.checkpoint["id"] == cp_res.checkpoint_id
    assert res.checkpoint["payload"]["decisions"] == ["Use stdio transport for MCP"]
    assert res.checkpoint["payload"]["test_status"]["summary"] == "8 passed"
    assert res.checkpoint["payload"]["test_status"]["provenance"] == "reported"
    assert res.checkpoint["payload"]["operator_note"] == "Initial milestone"
    store.close()


def test_get_repository_status(tmp_path: Path) -> None:
    facade, store, _, _ = _setup_test_env(tmp_path)
    res = facade.get_repository_status()
    assert "available" in res
    assert "status" in res
    store.close()


def test_set_current_work(tmp_path: Path) -> None:
    facade, store, task, _ = _setup_test_env(tmp_path)
    res = facade.set_current_work("Refactoring MCP facade")
    assert res.current_work == "Refactoring MCP facade"

    # Verify persisted in database
    t = store.get_task(task.id)
    assert t is not None
    assert t.current_work == "Refactoring MCP facade"

    # Clearing current work
    res_clear = facade.set_current_work(None)
    assert res_clear.current_work is None
    store.close()


def test_mark_completed_removes_from_remaining(tmp_path: Path) -> None:
    facade, store, task, _ = _setup_test_env(tmp_path)
    assert "Write tests" in task.remaining
    res = facade.mark_completed(["Write tests", "New completed item"])
    assert "Write tests" in res.completed
    assert "New completed item" in res.completed
    assert "Write tests" not in res.remaining
    assert "Verify coverage" in res.remaining

    t = store.get_task(task.id)
    assert t is not None
    assert "Write tests" in t.completed
    assert "Write tests" not in t.remaining
    store.close()


def test_add_remaining(tmp_path: Path) -> None:
    facade, store, task, _ = _setup_test_env(tmp_path)
    res = facade.add_remaining(["Audit security", "Benchmarking"])
    assert "Audit security" in res.remaining
    assert "Benchmarking" in res.remaining

    t = store.get_task(task.id)
    assert t is not None
    assert "Audit security" in t.remaining
    store.close()


def test_record_issue(tmp_path: Path) -> None:
    facade, store, task, _ = _setup_test_env(tmp_path)
    res = facade.record_issue(["Intermittent file lock timeout"])
    assert "Intermittent file lock timeout" in res.known_issues

    t = store.get_task(task.id)
    assert t is not None
    assert "Intermittent file lock timeout" in t.known_issues
    store.close()


def test_record_decision_creates_checkpoint(tmp_path: Path) -> None:
    facade, store, task, _ = _setup_test_env(tmp_path)
    res = facade.record_decision("Adopt SQLite WAL mode for local concurrency")
    assert res.checkpoint_id.startswith("cp_")
    assert res.decision == "Adopt SQLite WAL mode for local concurrency"

    cp = store.get_checkpoint(res.checkpoint_id)
    assert cp is not None
    assert "Adopt SQLite WAL mode for local concurrency" in cp.payload.decisions
    store.close()


def test_create_checkpoint(tmp_path: Path) -> None:
    facade, store, task, _ = _setup_test_env(tmp_path)
    res = facade.create_checkpoint(
        decisions=["Decided on standard stdio transport"],
        test_summary="15 unit tests passing",
        note="Mid-task milestone checkpoint",
    )
    assert res.checkpoint_id.startswith("cp_")
    assert res.decisions == ["Decided on standard stdio transport"]
    assert res.test_summary == "15 unit tests passing"
    assert res.test_provenance == "reported"
    assert res.note == "Mid-task milestone checkpoint"

    cp = store.get_checkpoint(res.checkpoint_id)
    assert cp is not None
    assert cp.payload.decisions == ["Decided on standard stdio transport"]
    assert cp.payload.test_status.summary == "15 unit tests passing"
    assert cp.payload.test_status.provenance == CheckpointTestProvenance.REPORTED
    store.close()


def test_write_tools_reject_unmanaged_or_read_only(tmp_path: Path) -> None:
    # Unmanaged
    facade_unm, store_unm, _, _ = _setup_test_env(tmp_path, managed=False)
    with pytest.raises(McpReadOnlyError):
        facade_unm.set_current_work("Hack")
    with pytest.raises(McpReadOnlyError):
        facade_unm.mark_completed(["Done"])
    with pytest.raises(McpReadOnlyError):
        facade_unm.add_remaining(["Later"])
    with pytest.raises(McpReadOnlyError):
        facade_unm.record_issue(["Bug"])
    with pytest.raises(McpReadOnlyError):
        facade_unm.record_decision("Choice")
    with pytest.raises(McpReadOnlyError):
        facade_unm.create_checkpoint()
    store_unm.close()

    # Managed but explicit read-only
    facade_ro, store_ro, _, _ = _setup_test_env(tmp_path, managed=True, read_only=True)
    with pytest.raises(McpReadOnlyError):
        facade_ro.set_current_work("Hack")
    store_ro.close()


def test_write_tools_input_bounds_validation(tmp_path: Path) -> None:
    facade, store, _, _ = _setup_test_env(tmp_path)

    # current_work too long
    with pytest.raises(ValueError, match="exceeds maximum length"):
        facade.set_current_work("x" * (MAX_CURRENT_WORK_CHARS + 1))

    # item too long
    with pytest.raises(ValueError, match="exceeds maximum length"):
        facade.mark_completed(["x" * (MAX_ITEM_CHARS + 1)])

    # empty items
    with pytest.raises(ValueError, match="No non-empty items"):
        facade.mark_completed(["", "   "])

    # too many items
    with pytest.raises(ValueError, match="Too many items"):
        facade.add_remaining(["item"] * (MAX_ITEMS_PER_CALL + 1))

    # decision too long
    with pytest.raises(ValueError, match="exceeds maximum length"):
        facade.record_decision("d" * (MAX_DECISION_CHARS + 1))

    # empty decision
    with pytest.raises(ValueError, match="cannot be empty"):
        facade.record_decision("   ")

    store.close()
