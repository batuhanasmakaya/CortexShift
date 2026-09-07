"""Refresh behaviour: explicit, timer-driven, and race-safe."""

import threading
import time
from pathlib import Path

import pytest
from textual.widgets import DataTable
from textual.worker import WorkerState

from cortexshift.application.repository_service import RepositoryService
from cortexshift.application.status_service import ProjectStatusService
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.status import ProjectStatus
from cortexshift.tui.app import STATE_WORKER_GROUP
from cortexshift.tui.screens import TuiSection
from cortexshift.tui.screens.sessions import SessionsSection
from tests.factories import make_inspection
from tests.tui.conftest import (
    active_task,
    assert_section,
    build_app,
    repository,
    settle,
    state,
    wait_for_state,
    wait_until,
    widget_text,
)


class SlowRepositoryService(RepositoryService):
    """A repository service whose inspections finish only when released.

    Lets a test start two inspections and complete them out of order, which is exactly
    the race the dashboard must not lose.
    """

    def __init__(self) -> None:
        self.branches: list[str] = []
        self.gates: list[threading.Event] = []
        self.started = threading.Semaphore(0)
        self._lock = threading.Lock()

    def queue(self, branch: str) -> threading.Event:
        """Queue the branch name the next inspection should report."""
        gate = threading.Event()
        with self._lock:
            self.branches.append(branch)
            self.gates.append(gate)
        return gate

    def inspect_repository(self, start_path: Path | None = None) -> RepositoryInspection:
        """Block until the matching gate is released, then return that inspection."""
        with self._lock:
            index = len(self.branches) - len(self.gates)
            branch = self.branches[index]
            gate = self.gates.pop(0)
        self.started.release()
        gate.wait(timeout=10)
        inspection = make_inspection(project_root=str(start_path or "/repo"))
        assert inspection.snapshot is not None
        return inspection.model_copy(
            update={"snapshot": inspection.snapshot.model_copy(update={"branch": branch})}
        )


@pytest.mark.asyncio
async def test_explicit_refresh_picks_up_state_changed_outside_the_dashboard(
    project: Path,
) -> None:
    """Pressing r reloads persisted state without reopening the app."""
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert len(active_task(app).remaining) == 2

        # Something else — another agent, or the CLI — advances the canonical task.
        TaskWorkspaceService().mark_completed(["Wire the parser"], start_dir=project)

        await pilot.press("r")
        await settle(app, pilot)

        refreshed = active_task(app)
        assert list(refreshed.remaining) == ["Add regression tests"]
        assert "Wire the parser" in refreshed.completed
        assert "2/3" in widget_text(app, "#overview-progress")


@pytest.mark.asyncio
async def test_lightweight_timer_reflects_state_written_through_mcp(project: Path) -> None:
    """An agent self-reporting through MCP shows up in an already-open dashboard.

    The MCP write path and the dashboard read path share one SQLite database, so no
    transcript scraping or provider cooperation is involved.
    """
    from cortexshift.adapters.sqlite.store import SQLiteStateStore
    from cortexshift.mcp.context import McpExecutionContext
    from cortexshift.mcp.facade import McpApplicationFacade

    app = build_app(project, refresh_seconds=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert len(active_task(app).remaining) == 2
        assert len(active_task(app).known_issues) == 1

        store = SQLiteStateStore(project / ".cortexshift" / "state.sqlite3", auto_migrate=False)
        try:
            db_project = store.get_default_project()
            assert db_project is not None
            task_id = store.get_active_task_id(db_project.id)
            assert task_id is not None
            mcp = McpApplicationFacade(
                McpExecutionContext(
                    project_root=project,
                    project_id=db_project.id,
                    task_id=task_id,
                    session_id="sess_mcp_agent",
                    provider_id=PROVIDER_CLAUDE,
                    managed_session=True,
                    read_only=False,
                ),
                store,
            )
            mcp.mark_completed(["Wire the parser"])
            mcp.record_issue(["Parser chokes on unicode paths"])
        finally:
            store.close()
        reported_at = utc_now()

        # No explicit refresh: the lightweight timer alone must surface the change. The
        # wait is on the state the dashboard publishes, never on a refresh worker, which
        # the next tick may supersede.
        await wait_for_state(
            app,
            pilot,
            lambda snapshot: (
                snapshot.active_task is not None
                and len(snapshot.active_task.remaining) == 1
                and len(snapshot.active_task.known_issues) == 2
            ),
            since=reported_at,
            description="surfaced the MCP write",
        )

        final = active_task(app)
        assert list(final.remaining) == ["Add regression tests"]
        assert "Wire the parser" in final.completed
        assert "Parser chokes on unicode paths" in final.known_issues
        assert "Parser chokes on unicode paths" in widget_text(app, "#task-issues")


@pytest.mark.asyncio
async def test_lightweight_refresh_never_runs_git(project: Path) -> None:
    """The 2-second state timer is SQLite-only; Git is not polled."""
    inspections = {"count": 0}

    class CountingRepositoryService(RepositoryService):
        def __init__(self) -> None:
            pass

        def inspect_repository(self, start_path: Path | None = None) -> RepositoryInspection:
            inspections["count"] += 1
            return make_inspection(project_root=str(start_path or project))

    app = build_app(project, refresh_seconds=0.05, repository_service=CountingRepositoryService())
    async with app.run_test() as pilot:
        await settle(app, pilot)
        after_startup = inspections["count"]
        assert after_startup == 1  # one inspection at startup

        for _ in range(10):
            await pilot.pause(0.05)
        await settle(app, pilot)

        # Many state ticks elapsed; Git was never re-inspected.
        assert inspections["count"] == after_startup
        assert state(app).project.name == "Luna"


@pytest.mark.asyncio
async def test_git_is_refreshed_on_repository_screen_entry(project: Path) -> None:
    inspections = {"count": 0}

    class CountingRepositoryService(RepositoryService):
        def __init__(self) -> None:
            pass

        def inspect_repository(self, start_path: Path | None = None) -> RepositoryInspection:
            inspections["count"] += 1
            return make_inspection(project_root=str(start_path or project))

    app = build_app(project, repository_service=CountingRepositoryService())
    async with app.run_test() as pilot:
        await settle(app, pilot)
        baseline = inspections["count"]

        await pilot.press("3")
        await settle(app, pilot)
        assert inspections["count"] == baseline + 1

        # Moving to a non-repository screen does not re-inspect.
        await pilot.press("4")
        await settle(app, pilot)
        assert inspections["count"] == baseline + 1


@pytest.mark.asyncio
async def test_a_stale_repository_result_never_overwrites_a_newer_one(project: Path) -> None:
    """Out-of-order worker results must not put older data back on screen."""
    slow = SlowRepositoryService()
    first = slow.queue("stale-branch")
    app = build_app(project, repository_service=slow)

    async with app.run_test() as pilot:
        # Wait for the startup inspection to be in flight.
        assert slow.started.acquire(timeout=10)

        second = slow.queue("fresh-branch")
        app.refresh_repository()
        await pilot.pause()
        assert slow.started.acquire(timeout=10)

        # The newer inspection finishes first...
        second.set()
        await settle(app, pilot)
        assert repository(app).branch == "fresh-branch"

        # ...and the older one lands afterwards. It must be discarded.
        first.set()
        await settle(app, pilot)
        assert repository(app).branch == "fresh-branch"

        # The dashboard stayed responsive throughout.
        assert app.is_running
        await pilot.press("2")
        await pilot.pause()
        assert_section(app, TuiSection.TASK)


@pytest.mark.asyncio
async def test_repeated_refreshes_leave_the_newest_state_on_screen(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        for _ in range(5):
            await pilot.press("r")
        await settle(app, pilot, rounds=5)

        assert state(app).project.name == "Luna"
        assert repository(app).status is RepositoryInspectionStatus.READY
        assert app.is_running


@pytest.mark.asyncio
async def test_table_selection_survives_a_refresh(project: Path) -> None:
    from cortexshift.domain.provider import PROVIDER_CODEX
    from tests.tui.conftest import seed_session

    task = TaskWorkspaceService().get_active_task(project)
    assert task is not None
    seed_session(project, task.id, provider_id=PROVIDER_CLAUDE, native_session_id="a")
    second = seed_session(project, task.id, provider_id=PROVIDER_CODEX, native_session_id="b")

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.show_section(TuiSection.SESSIONS)
        await settle(app, pilot)

        table = app.query_one("#sessions-table", DataTable)
        table.move_cursor(row=table.get_row_index(second.id), animate=False)
        await pilot.pause()

        await pilot.press("r")
        await settle(app, pilot)

        section = app.section_view(TuiSection.SESSIONS)
        assert isinstance(section, SessionsSection)
        assert section.selected_session is not None
        assert section.selected_session.id == second.id


class AlwaysSlowStatusService(ProjectStatusService):
    """A status read that always takes longer than the refresh tick under test.

    Reproduces the machine the dashboard has to survive: one where reading state costs
    more than the interval between ticks. No sleep is used to hide a race here -- the
    delay *is* the condition under test.
    """

    def __init__(self, delay: float) -> None:
        super().__init__()
        self._delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def get_status(self, start_dir: Path | str | None = None) -> ProjectStatus:
        with self._lock:
            self.calls += 1
        time.sleep(self._delay)
        return super().get_status(start_dir)


@pytest.mark.asyncio
async def test_the_timer_does_not_start_a_reload_on_top_of_a_running_one(project: Path) -> None:
    """Ticks that arrive mid-reload are skipped, not queued behind a cancellation.

    Without the skip the timer cancels the reload that was about to finish, the
    generation guard drops its result, and the next tick does the same -- so on a machine
    where reading state outruns the interval the dashboard silently never updates again.
    """
    slow = AlwaysSlowStatusService(delay=0.1)
    app = build_app(project, refresh_seconds=0.02, status_service=slow)

    async with app.run_test() as pilot:
        # The dashboard publishes state even though every reload outlives several ticks.
        await wait_until(
            pilot,
            lambda: app._snapshot is not None,
            description="the dashboard published state despite a slow reload",
        )

        # And it did so without stacking reloads: far fewer than one per tick elapsed.
        await pilot.pause(0.3)
        running = [
            w
            for w in app.workers
            if w.group == STATE_WORKER_GROUP and w.state is WorkerState.RUNNING
        ]
        assert len(running) <= 1, "the timer started a reload while one was already running"


@pytest.mark.asyncio
async def test_an_operator_refresh_still_supersedes_a_running_reload(project: Path) -> None:
    """Skipping applies to the timer only: asking for fresh data now still preempts."""
    slow = AlwaysSlowStatusService(delay=0.3)
    app = build_app(project, refresh_seconds=60.0, status_service=slow)

    async with app.run_test() as pilot:
        await wait_until(
            pilot,
            lambda: any(w.group == STATE_WORKER_GROUP for w in app.workers),
            description="the mount reload starts",
        )
        in_flight = next(w for w in app.workers if w.group == STATE_WORKER_GROUP)

        await pilot.press("r")
        await wait_until(
            pilot,
            lambda: in_flight.state is WorkerState.CANCELLED,
            description="the operator's refresh supersedes the running reload",
        )
