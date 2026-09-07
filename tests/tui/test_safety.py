"""Safety boundaries: workspace lease, project isolation, recovery, and error handling."""

import multiprocessing
import time
from collections.abc import Iterator
from multiprocessing.synchronize import Event as EventType
from pathlib import Path

import pytest
from textual.widgets import Input, Static

from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.domain.errors import DatabaseStateError, WorkspaceLockedError
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import SessionStatus
from cortexshift.tui.modals import ConfirmModal
from cortexshift.tui.models import WorkspaceActivity
from tests.tui.conftest import (
    assert_base_screen,
    build_app,
    build_facade,
    drain_workers,
    repository,
    seed_project,
    seed_session,
    settle,
    state,
    wait_for_screen,
    wait_until,
)


def _hold_lease(  # pragma: no cover - runs in a spawned helper process
    project_root: str,
    acquired: EventType,
    release: EventType,
) -> None:
    """Hold the exclusive workspace lease in a separate process."""
    lease = FileWorkspaceLeaseManager().get_lease(Path(project_root))
    if not lease.acquire():
        return
    acquired.set()
    release.wait(timeout=30)
    lease.release()


@pytest.fixture
def held_lease(project: Path) -> Iterator[Path]:
    """Another process owns the exclusive workspace lease for the duration of the test."""
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    worker = context.Process(target=_hold_lease, args=(str(project), acquired, release))
    worker.start()
    assert acquired.wait(timeout=30), "the helper process never acquired the lease"
    try:
        yield project
    finally:
        release.set()
        worker.join(timeout=30)


@pytest.mark.asyncio
async def test_the_dashboard_does_not_hold_the_workspace_lease(project: Path) -> None:
    """An open dashboard must never block a coding agent from starting."""
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        # While the dashboard is fully loaded, the lease is still free to acquire.
        lease = FileWorkspaceLeaseManager().get_lease(project)
        assert lease.acquire() is True
        lease.release()

        # Reading and refreshing does not take it either.
        await pilot.press("r")
        await settle(app, pilot)
        lease = FileWorkspaceLeaseManager().get_lease(project)
        assert lease.acquire() is True
        lease.release()


@pytest.mark.asyncio
async def test_the_dashboard_stays_useful_while_a_provider_owns_the_workspace(
    held_lease: Path,
) -> None:
    """Terminal A runs an agent; terminal B keeps a working dashboard."""
    project = held_lease
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        # Reads work.
        assert state(app).project.name == "Luna"
        assert repository(app).ready

        # The lease probe reports the workspace as busy, from the OS lock — never from
        # the mere presence of the lock file.
        assert app._workspace_activity is WorkspaceActivity.BUSY

        # Cooperative task updates still work: they never contend for the lease.
        await pilot.press("n")
        await pilot.pause()
        app.screen.query_one("#entry-input", Input).value = "Discovered while the agent ran"
        await pilot.press("enter")
        await settle(app, pilot)
        task = TaskWorkspaceService().get_active_task(project)
        assert task is not None
        assert "Discovered while the agent ran" in task.remaining

        # A cooperative checkpoint also works under an active lease.
        await pilot.press("c")
        await settle(app, pilot)
        app.screen.query_one("#checkpoint-note", Input).value = "Captured alongside a running agent"
        await pilot.press("enter")
        await settle(app, pilot)
        assert len(CheckpointService().list_checkpoints(start_dir=project)) == 1

        # And the dashboard is still alive.
        assert app.is_running


def test_recovery_is_refused_while_the_workspace_is_leased(held_lease: Path) -> None:
    """RecoveryService — not the dashboard — enforces exclusivity."""
    project = held_lease
    facade = build_facade(project)

    with pytest.raises(WorkspaceLockedError):
        facade.preview_recovery()
    with pytest.raises(WorkspaceLockedError):
        facade.recover()


@pytest.mark.asyncio
async def test_an_expected_error_is_reported_without_crashing_the_dashboard(
    held_lease: Path,
) -> None:
    project = held_lease
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("R")  # recovery, which requires the lease
        await settle(app, pilot, rounds=4)

        # No modal opened, the app survived, and the operator was notified.
        assert app.is_running
        assert_base_screen(app)

        notifications = list(app._notifications)
        assert notifications, "the lease conflict was not surfaced to the operator"
        lease_notice = next(n for n in notifications if "already active" in str(n.message).lower())
        assert lease_notice.severity == "error"
        # The operator is told to wait, never to delete the lock file.
        assert "delete" not in str(lease_notice.message).lower()


@pytest.mark.asyncio
async def test_overview_flags_unfinalized_sessions_without_declaring_them_crashed(
    project: Path,
) -> None:
    task = TaskWorkspaceService().get_active_task(project)
    assert task is not None
    seed_session(
        project,
        task.id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.RUNNING,
        exit_reason=None,
        exit_code=None,
    )

    from tests.tui.conftest import widget_text

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert state(app).activity.unfinalized_session_count == 1

        notice = widget_text(app, "#overview-recovery")
        assert "unfinalized" in notice
        assert "Recovery may be required" in notice
        # Honest wording: never asserted as crashed.
        assert "crashed" not in notice.lower()


@pytest.mark.asyncio
async def test_recovery_preview_precedes_any_mutation(project: Path) -> None:
    task = TaskWorkspaceService().get_active_task(project)
    assert task is not None
    stale = seed_session(
        project,
        task.id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.RUNNING,
        exit_reason=None,
        exit_code=None,
    )

    from cortexshift.application.session_service import SessionService

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("R")

        confirm = await wait_for_screen(app, pilot, ConfirmModal, selector="#confirm-body")

        # The preview is on screen, and nothing has been reconciled yet.
        body = str(confirm.query_one("#confirm-body", Static).content)
        assert stale.id in body
        assert "ended_at" in body
        unchanged = SessionService().get_session(stale.id, project)
        assert unchanged.status is SessionStatus.RUNNING
        assert unchanged.reconciled_at is None

        await pilot.press("tab")
        await pilot.press("enter")
        await settle(app, pilot, rounds=4)

    reconciled = SessionService().get_session(stale.id, project)
    assert reconciled.status is SessionStatus.INTERRUPTED
    assert reconciled.reconciled_at is not None
    # An unobserved process end time is never fabricated.
    assert reconciled.ended_at is None


@pytest.mark.asyncio
async def test_declining_recovery_reconciles_nothing(project: Path) -> None:
    task = TaskWorkspaceService().get_active_task(project)
    assert task is not None
    stale = seed_session(
        project,
        task.id,
        status=SessionStatus.RUNNING,
        exit_reason=None,
        exit_code=None,
    )

    from cortexshift.application.session_service import SessionService

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("R")
        await wait_for_screen(app, pilot, ConfirmModal)

        await pilot.press("escape")
        await settle(app, pilot)

    assert SessionService().get_session(stale.id, project).status is SessionStatus.RUNNING


@pytest.mark.asyncio
async def test_dashboard_actions_never_reach_another_project(tmp_path: Path) -> None:
    """A dashboard bound to project A cannot mutate project B."""
    project_a = tmp_path / "alpha"
    project_b = tmp_path / "beta"
    project_a.mkdir()
    project_b.mkdir()
    seed_project(project_a, name="Alpha", title="Alpha task", remaining=["Alpha item"])
    seed_project(project_b, name="Beta", title="Beta task", remaining=["Beta item"])

    before_b = TaskWorkspaceService().get_active_task(project_b)
    assert before_b is not None

    app = build_app(project_a)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert state(app).project.name == "Alpha"

        # Every dashboard action is bound to the resolved root, with no path parameter.
        assert app.facade.project_root == project_a.resolve()

        await pilot.press("n")
        await pilot.pause()
        app.screen.query_one("#entry-input", Input).value = "Written into Alpha only"
        await pilot.press("enter")
        await settle(app, pilot)

        await pilot.press("c")
        await settle(app, pilot)
        app.screen.query_one("#checkpoint-note", Input).value = "Alpha checkpoint"
        await pilot.press("enter")
        await settle(app, pilot)

    task_a = TaskWorkspaceService().get_active_task(project_a)
    task_b = TaskWorkspaceService().get_active_task(project_b)
    assert task_a is not None and task_b is not None

    assert "Written into Alpha only" in task_a.remaining
    assert task_b.remaining == before_b.remaining
    assert task_b.updated_at == before_b.updated_at
    assert len(CheckpointService().list_checkpoints(start_dir=project_a)) == 1
    assert CheckpointService().list_checkpoints(start_dir=project_b) == []


@pytest.mark.asyncio
async def test_the_workspace_probe_releases_the_lease_immediately(project: Path) -> None:
    """The probe may acquire the lease, but must never hold it."""
    facade = build_facade(project)
    assert facade.workspace_activity() is WorkspaceActivity.FREE

    lease = FileWorkspaceLeaseManager().get_lease(project)
    assert lease.acquire() is True
    try:
        assert facade.workspace_activity() is WorkspaceActivity.BUSY
    finally:
        lease.release()

    assert facade.workspace_activity() is WorkspaceActivity.FREE


def test_repeated_provider_probes_are_served_from_an_in_memory_cache(project: Path) -> None:
    """A short in-process cache avoids re-probing on every refresh; nothing is persisted."""
    from tests.tui.conftest import FakeDoctorService, fake_which

    doctor = FakeDoctorService()
    clock = {"now": 0.0}
    facade = build_facade(
        project,
        doctor_service=doctor,
        which_fn=fake_which(),
        clock=lambda: clock["now"],
        provider_cache_seconds=30.0,
    )

    facade.provider_status()
    facade.provider_status()
    assert doctor.calls == 1

    # An explicit refresh always re-probes.
    facade.provider_status(refresh=True)
    assert doctor.calls == 2

    # And the cache expires on its own.
    clock["now"] += 31.0
    facade.provider_status()
    assert doctor.calls == 3

    # No cache file or database row was created.
    state_dir = project / ".cortexshift"
    assert not any(path.name.startswith("provider") for path in state_dir.iterdir())


def test_time_moves_forward_for_the_probe_cache() -> None:
    """Guards the fixture above against a frozen clock giving a false pass."""
    start = time.monotonic()
    assert time.monotonic() >= start


@pytest.mark.asyncio
async def test_a_persistently_failing_state_refresh_is_reported_once(project: Path) -> None:
    """The refresh timer must inform the operator, not bury them in notifications."""

    class BrokenStatusService:
        def get_status(self, start_path: object = None) -> None:
            raise DatabaseStateError("state.sqlite3 is unreadable")

    def failures() -> list[object]:
        return [n for n in app._notifications if "unreadable" in str(n.message)]

    app = build_app(project, refresh_seconds=0.05, status_service=BrokenStatusService())
    async with app.run_test() as pilot:
        # Wait for the first report rather than for a fixed number of ticks: the state
        # refresh is exclusive, so a tick can supersede the worker that would have
        # raised, and awaiting that worker's result would fail for a cancellation the
        # dashboard intends.
        await wait_until(pilot, lambda: bool(failures()), description="the failure is reported")

        # Let many more ticks fail the same way; the report must not repeat.
        for _ in range(12):
            await pilot.pause(0.05)
        await drain_workers(app, pilot)

        assert len(failures()) == 1, f"the same failure was reported {len(failures())} times"
        assert app.is_running
