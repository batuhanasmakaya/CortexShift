"""Integration tests verifying MCP operations coexist with active workspace lease."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLease, FileWorkspaceLeaseManager
from cortexshift.domain.errors import WorkspaceLockedError
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade


def test_mcp_mutations_succeed_while_workspace_lease_held(tmp_path: Path) -> None:
    """Invariant: MCP tools must never acquire agent.lock, avoiding self-deadlock."""
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(db_file)

    proj = Project(name="LeaseCoexistRepo", repo_path=str(tmp_path))
    store.save_project(proj)

    task = Task(
        project_id=proj.id,
        title="Lease Coexistence Task",
        objective="Verify MCP writes succeed during active lease",
        completed_items=["Pre-flight"],
        remaining_items=["Execute with lock", "Post-verification"],
    )
    store.save_task(task)
    store.set_active_task_id(proj.id, task.id)

    sess = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
    store.save_session(sess)

    lease_mgr = FileWorkspaceLeaseManager()

    # Acquire the exclusive workspace lease as if a provider process is running
    holder_lease = lease_mgr.get_lease(tmp_path)
    acquired = holder_lease.acquire()
    assert acquired is True

    # A secondary lease acquisition must fail
    second_lease = FileWorkspaceLease(tmp_path / ".cortexshift" / "agent.lock")
    assert second_lease.acquire() is False
    with pytest.raises(WorkspaceLockedError), second_lease:
        pass

    try:
        # Construct MCP facade in managed writable context
        ctx = McpExecutionContext(
            project_root=tmp_path,
            project_id=proj.id,
            task_id=task.id,
            session_id=sess.id,
            provider_id=sess.provider_id,
            managed_session=True,
            read_only=False,
        )
        facade = McpApplicationFacade(ctx, store)

        # Execute MCP write tools: they MUST bypass agent.lock and succeed
        res_work = facade.set_current_work("Performing work under active lease")
        assert res_work.current_work == "Performing work under active lease"

        res_comp = facade.mark_completed(["Execute with lock"])
        assert "Execute with lock" in res_comp.completed
        assert "Execute with lock" not in res_comp.remaining

        res_dec = facade.record_decision("Lock bypass invariant verified")
        assert res_dec.checkpoint_id.startswith("cp_")

        res_cp = facade.create_checkpoint(
            decisions=["Second decision"],
            test_summary="All tests pass",
            note="Cooperative checkpoint under lease",
        )
        assert res_cp.checkpoint_id.startswith("cp_")
    finally:
        holder_lease.release()
        store.close()

    # Reopen store and verify persistence
    verify_store = SQLiteStateStore(db_file)
    t = verify_store.get_task(task.id)
    assert t is not None
    assert t.current_work == "Performing work under active lease"
    assert "Execute with lock" in t.completed
    assert "Post-verification" in t.remaining

    latest_cp = verify_store.get_latest_checkpoint(task.id)
    assert latest_cp is not None
    assert "Second decision" in latest_cp.payload.decisions
    verify_store.close()
