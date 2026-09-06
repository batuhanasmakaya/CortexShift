"""An agent writes canonical state; the human watching the dashboard sees it.

No transcript is read and no provider is contacted. The agent reports through MCP, the
report lands in SQLite, and the dashboard's lightweight refresh surfaces it.
"""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade
from cortexshift.tui.screens import TuiSection
from tests.tui.conftest import (
    active_task,
    build_app,
    make_git_repo,
    seed_project,
    seed_session,
    settle,
    state,
    widget_text,
)


def agent_facade(root: Path, session_id: str) -> tuple[McpApplicationFacade, SQLiteStateStore]:
    """Build the MCP facade exactly as a launched agent's server would bind it."""
    store = SQLiteStateStore(root / ".cortexshift" / "state.sqlite3", auto_migrate=False)
    project = store.get_default_project()
    assert project is not None
    task_id = store.get_active_task_id(project.id)
    assert task_id is not None
    context = McpExecutionContext(
        project_root=root,
        project_id=project.id,
        task_id=task_id,
        session_id=session_id,
        provider_id=PROVIDER_CLAUDE,
        managed_session=True,
        read_only=False,
    )
    return McpApplicationFacade(context, store), store


@pytest.mark.asyncio
async def test_an_open_dashboard_reflects_agent_progress_reported_over_mcp(
    tmp_path: Path,
) -> None:
    root = tmp_path / "live"
    root.mkdir()
    make_git_repo(root)
    task = seed_project(
        root,
        remaining=["Wire the parser", "Add regression tests"],
        completed=None,
        issues=None,
        current_work=None,
    )
    session = seed_session(root, task.id)

    app = build_app(root, refresh_seconds=0.05)
    async with app.run_test(size=(110, 40)) as pilot:
        await settle(app, pilot)

        initial = active_task(app)
        assert len(initial.remaining) == 2
        assert len(initial.known_issues) == 0
        assert initial.current_work is None

        # The agent self-reports mid-flight, exactly as it would through MCP tools.
        facade, store = agent_facade(root, session.id)
        try:
            facade.set_current_work("Parsing multi-monitor geometry")
            facade.mark_completed(["Wire the parser"])
            facade.record_issue(["Parser mis-handles rotated displays"])
        finally:
            store.close()

        # The dashboard is never told; its own refresh timer must surface the change.
        for _ in range(60):
            await pilot.pause(0.05)
            await app.workers.wait_for_complete()
            await pilot.pause()
            current = active_task(app)
            if len(current.remaining) == 1 and len(current.known_issues) == 1:
                break

        task_view = active_task(app)
        assert task_view.current_work == "Parsing multi-monitor geometry"
        assert list(task_view.completed) == ["Wire the parser"]
        assert list(task_view.remaining) == ["Add regression tests"]
        assert list(task_view.known_issues) == ["Parser mis-handles rotated displays"]

        # Overview progress moved from 0/2 to 1/2 without any operator action.
        assert "1/2" in widget_text(app, "#overview-progress")

        app.show_section(TuiSection.TASK)
        await settle(app, pilot)
        assert "Parser mis-handles rotated displays" in widget_text(app, "#task-issues")
        assert "Parsing multi-monitor geometry" in widget_text(app, "#task-current-work")


@pytest.mark.asyncio
async def test_an_agent_checkpoint_appears_in_the_open_dashboard(tmp_path: Path) -> None:
    root = tmp_path / "live-checkpoint"
    root.mkdir()
    make_git_repo(root)
    task = seed_project(root, remaining=["Something"])
    session = seed_session(root, task.id)

    app = build_app(root, refresh_seconds=0.05)
    async with app.run_test(size=(110, 40)) as pilot:
        await settle(app, pilot)
        assert len(state(app).checkpoints) == 0

        facade, store = agent_facade(root, session.id)
        try:
            facade.create_checkpoint(
                decisions=["Kept the parser provider-agnostic"],
                test_summary="12 passed",
                note="Reported by the agent",
            )
        finally:
            store.close()

        for _ in range(60):
            await pilot.pause(0.05)
            await app.workers.wait_for_complete()
            await pilot.pause()
            if state(app).checkpoints:
                break

        assert len(state(app).checkpoints) == 1

        app.show_section(TuiSection.CHECKPOINTS)
        await settle(app, pilot)
        detail = widget_text(app, "#checkpoints-detail")
        assert "12 passed" in detail
        # The agent reported it; CortexShift did not verify it.
        assert "Reported / unverified" in detail
        assert "Historical observation" in detail
