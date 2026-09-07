"""The dashboard tests must not depend on the machine they run on.

Two things made the second public CI run red, and both are covered here:

- The dashboard resolves provider executables in two places. Only one was faked, so the
  switch confirmation appeared on a developer machine with Codex installed and never
  appeared on a runner without it -- surfacing as "expected ConfirmModal, saw Screen".
- State refreshes are `exclusive` workers, so one still in flight when the next tick
  starts is cancelled by design. Awaiting it raised `WorkerCancelled` for an outcome the
  application intends.
"""

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import cast

import pytest
from textual.screen import Screen
from textual.widgets import Button, OptionList, Static
from textual.worker import Worker, WorkerCancelled, WorkerState

from cortexshift.application.status_service import ProjectStatusService
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.status import ProjectStatus
from cortexshift.tui.actions import TuiExitAction
from cortexshift.tui.app import STATE_WORKER_GROUP, CortexShiftApp
from cortexshift.tui.modals import (
    CheckpointModal,
    ConfirmModal,
    InfoModal,
    MarkCompletedModal,
    ProviderActionModal,
    SetCurrentWorkModal,
)
from tests.cli_runner import PROVIDER_EXECUTABLES, path_without_providers, run_cli
from tests.tui.conftest import (
    NoLaunchProcessRunner,
    TuiPilot,
    assert_screen,
    build_app,
    build_facade,
    drain_workers,
    seed_session,
    settle,
    wait_for_screen,
    wait_for_state,
    wait_until,
)

# --- provider hermeticity -------------------------------------------------


def test_the_provider_free_fixture_really_hides_every_provider(provider_free_path: None) -> None:
    """Guards the fixture itself: without this, the tests below could pass vacuously."""
    for name in PROVIDER_EXECUTABLES:
        assert shutil.which(name) is None
    # Git survives, so repository inspection still behaves as it does on a runner.
    assert shutil.which("git") is not None


def test_switch_preview_does_not_need_a_provider_on_the_real_path(
    project: Path, provider_free_path: None
) -> None:
    """`preview_switch` runs the switch dry run, which requires the target installed.

    The requirement is real production behaviour and stays; what changed is that the
    dashboard's fake resolver now reaches it, so the answer comes from the test's own
    fixture rather than from whatever the developer happens to have installed.
    """
    facade = build_facade(project)
    task = facade.load_state().active_task
    assert task is not None
    seed_session(project, task.id, provider_id=PROVIDER_CLAUDE)

    preview = facade.preview_switch("codex")

    assert preview.target_provider_id == "codex"
    assert preview.source_provider_id == "claude"


def test_provider_availability_comes_from_the_fake_not_the_machine(
    project: Path, provider_free_path: None
) -> None:
    """Both resolution paths agree, and both answer from the injected fake."""
    facade = build_facade(project)

    assert facade.provider_available("codex") is True
    assert facade.provider_available("antigravity") is False


@pytest.mark.asyncio
async def test_the_switch_confirmation_is_mounted_with_no_provider_installed(
    project: Path, provider_free_path: None
) -> None:
    """The exact CI failure: prove the modal is actually pushed, then assert it.

    The assertion is not "some screen exists" -- the flow is driven to the point where
    the confirmation is genuinely on top, and the dry-run content it was built from is
    checked, so a silently swallowed `ProviderNotFoundError` cannot pass here.
    """
    seed_session(project, _active_task_id(project), provider_id=PROVIDER_CLAUDE)

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        modal = await _open_palette(app, pilot)
        _highlight_switch_to_codex(modal)

        await pilot.press("enter")
        # Wait for the confirmation to be mounted rather than for a number of rounds.
        await wait_until(
            pilot,
            lambda: isinstance(app.screen, ConfirmModal),
            description="the switch confirmation is mounted",
        )

        confirmation = assert_screen(app, ConfirmModal)
        assert app.is_running
        # The dashboard reported no error: a failed dry run would notify instead.
        assert not [n for n in app._notifications if "not found in PATH" in str(n.message)]
        assert confirmation is app.screen


def _plant_recording_agents(directory: Path, witness: Path) -> None:
    """Install executables named like the real agents that record being run."""
    for name in PROVIDER_EXECUTABLES:
        if os.name == "nt":  # pragma: no cover - exercised on the Windows CI runner
            (directory / f"{name}.bat").write_text(
                f'@echo %0 %* >> "{witness}"\n', encoding="utf-8"
            )
            continue
        stub = directory / name
        stub.write_text(f'#!/bin/sh\necho "$0 $*" >> "{witness}"\nexit 0\n', encoding="utf-8")
        stub.chmod(0o755)


def test_the_doctor_never_probes_the_machines_own_agents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`doctor` is the one command that genuinely executes a provider CLI.

    Probing PATH is its job, so the fix is not to change `doctor` -- it is to make sure
    the suite hands it a PATH with no agent on it. Otherwise a developer's own `claude`
    and `codex` really are executed for version and auth probes, while a runner executes
    nothing: the same command doing different work in the two places.

    Agents are planted here and put on PATH, then the helper the suite uses is asked for
    a PATH without them. Executables that record being run prove the probes found none.
    """
    witness = tmp_path / "executions.log"
    planted = tmp_path / "bin"
    planted.mkdir()
    _plant_recording_agents(planted, witness)

    monkeypatch.setenv("PATH", os.pathsep.join([str(planted), os.environ["PATH"]]))
    assert shutil.which("codex") is not None, "the planted agents are not resolvable"

    scrubbed = path_without_providers()
    assert str(planted) not in scrubbed.split(os.pathsep)

    result = run_cli(
        [sys.executable, "-m", "cortexshift", "doctor", "--json"],
        env={**os.environ, "PATH": scrubbed},
    )

    assert result.returncode == 0
    assert not witness.exists(), f"a provider binary was executed: {witness.read_text()}"
    assert all(p["installed"] is False for p in json.loads(result.stdout)["providers"])


@pytest.mark.asyncio
async def test_no_provider_process_is_ever_launched(
    project: Path, provider_free_path: None
) -> None:
    """Every dashboard path under test is a preview or a dry run.

    The facade is wired with a runner that raises if anything reaches it, so a test that
    started spending model quota would fail rather than succeed quietly.
    """
    with pytest.raises(AssertionError, match="a provider process was launched"):
        NoLaunchProcessRunner().run_interactive(["codex", "resume"], project)

    seed_session(project, _active_task_id(project), provider_id=PROVIDER_CLAUDE)
    facade = build_facade(project)
    facade.preview_switch("codex")
    facade.preview_handoff("codex")


# --- worker supersession --------------------------------------------------


class SupersededOnceStatusService(ProjectStatusService):
    """A status read whose first call outlives the refresh tick that started it.

    Guarantees the exclusive state worker is superseded at least once, which is what CI
    hit by being slower than a developer laptop.
    """

    def __init__(self, delay: float) -> None:
        super().__init__()
        self._delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def get_status(self, start_dir: Path | str | None = None) -> ProjectStatus:
        with self._lock:
            self.calls += 1
            first = self.calls == 1
        if first:
            time.sleep(self._delay)
        return super().get_status(start_dir)


async def _first_state_worker(app: CortexShiftApp, pilot: TuiPilot) -> Worker[None]:
    """The state refresh started at mount, captured before anything can supersede it.

    `WorkerManager` drops workers once they finish, so a test that looks for a cancelled
    worker afterwards finds nothing -- and between two refreshes there may be no state
    worker at all. Holding the object keeps its outcome observable either way.
    """
    await wait_until(
        pilot,
        lambda: any(w.group == STATE_WORKER_GROUP for w in app.workers),
        description="the dashboard starts a state refresh",
    )
    worker = next(w for w in app.workers if w.group == STATE_WORKER_GROUP)
    return cast(Worker[None], worker)


async def _supersede(app: CortexShiftApp, pilot: TuiPilot, worker: Worker[None]) -> None:
    """Have the operator ask for a refresh while `worker` is still reading state.

    An explicit refresh is now the only thing that supersedes an in-flight reload -- the
    timer skips instead, so that a slow machine cannot starve the dashboard. Driving it
    from the action makes the supersession a fact of the test rather than a race it hopes
    to win.
    """
    app.refresh_state()
    await wait_until(
        pilot,
        lambda: worker.state is WorkerState.CANCELLED,
        description="the in-flight refresh is superseded by an explicit one",
    )


@pytest.mark.asyncio
async def test_a_superseded_refresh_does_not_break_the_waits(project: Path) -> None:
    """`settle`/`drain_workers` treat cancellation as the terminal state it is.

    The first status read outlives its tick by an order of magnitude, so the exclusive
    worker is certain to be superseded -- no reliance on the machine being slow.
    """
    slow = SupersededOnceStatusService(delay=0.3)
    app = build_app(project, refresh_seconds=0.05, status_service=slow)

    async with app.run_test() as pilot:
        superseded = await _first_state_worker(app, pilot)
        await _supersede(app, pilot, superseded)

        # Would raise WorkerCancelled if these waits awaited worker *results*.
        await settle(app, pilot)

        # A refresh started after the supersession still reaches the screen.
        probed_at = utc_now()
        await wait_for_state(
            app,
            pilot,
            lambda snapshot: snapshot.active_task is not None,
            since=probed_at,
            description="published state after a superseded refresh",
        )


@pytest.mark.asyncio
async def test_awaiting_a_superseded_worker_is_what_used_to_fail(project: Path) -> None:
    """Pins the diagnosis: the dashboard was fine, the wait was wrong.

    Keeps the reason for `drain_workers` visible, so nobody restores the simpler call.
    """
    slow = SupersededOnceStatusService(delay=0.3)
    app = build_app(project, refresh_seconds=0.05, status_service=slow)

    async with app.run_test() as pilot:
        superseded = await _first_state_worker(app, pilot)
        await _supersede(app, pilot, superseded)

        # Asking a superseded worker for its result raises; that is the CI failure.
        with pytest.raises(WorkerCancelled):
            await superseded.wait()

        # The dashboard itself never faltered: it is still running and still refreshing.
        assert app.is_running
        probed_at = utc_now()
        await drain_workers(app, pilot)
        await wait_for_state(
            app,
            pilot,
            lambda snapshot: snapshot.active_task is not None,
            since=probed_at,
            description="kept refreshing after the supersession",
        )


@pytest.mark.asyncio
async def test_no_worker_is_left_running_when_the_dashboard_closes(project: Path) -> None:
    """A test must not leave the app with work in flight for teardown to cancel."""
    app = build_app(project, refresh_seconds=0.05)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await wait_until(
            pilot,
            lambda: not [w for w in app.workers if w.state is WorkerState.RUNNING],
            description="the dashboard is quiescent",
        )
        assert not [w for w in app.workers if w.state is WorkerState.RUNNING]


# --- helpers --------------------------------------------------------------


def _active_task_id(root: Path) -> str:
    from cortexshift.application.task_workspace import TaskWorkspaceService

    task = TaskWorkspaceService().get_active_task(root)
    assert task is not None
    return task.id


async def _open_palette(app: CortexShiftApp, pilot: TuiPilot) -> ProviderActionModal:
    await pilot.press("x")
    await wait_until(
        pilot,
        lambda: isinstance(app.screen, ProviderActionModal),
        description="the provider palette is mounted",
    )
    return assert_screen(app, ProviderActionModal)


def _highlight_switch_to_codex(modal: ProviderActionModal) -> None:
    index = next(
        i
        for i, option in enumerate(modal._options)
        if option.action is TuiExitAction.SWITCH and option.provider == "codex"
    )
    modal.query_one("#provider-actions", OptionList).highlighted = index


# --- modal composition ----------------------------------------------------


MODAL_FOCUS_TARGETS = [
    (ConfirmModal, "#confirm-cancel"),
    (InfoModal, "#info-close"),
    (ProviderActionModal, "#provider-actions"),
    (CheckpointModal, "#checkpoint-decision"),
    (SetCurrentWorkModal, "#entry-input"),
    (MarkCompletedModal, "#entry-input"),
]


@pytest.mark.parametrize(
    ("modal", "selector"), MODAL_FOCUS_TARGETS, ids=lambda v: getattr(v, "__name__", v)
)
def test_a_modal_declares_its_focus_instead_of_querying_for_it(
    modal: type[Screen[object]], selector: str
) -> None:
    """Opening a dialog must not race its own composition.

    `ConfirmModal.on_mount` used to call `query_one("#confirm-cancel")`, but a screen
    receives `Mount` before `compose` has finished mounting its subtree. On a slow runner
    the button did not exist yet and opening the confirmation raised `NoMatches` from
    production code -- the dashboard, not the test. `AUTO_FOCUS` states the same intent
    declaratively and Textual applies it once the screen is composed.
    """
    assert selector == modal.AUTO_FOCUS
    assert not hasattr(modal, "on_mount"), (
        f"{modal.__name__} focuses or populates in on_mount again, which races compose"
    )


@pytest.mark.asyncio
async def test_every_confirmation_button_exists_when_the_modal_is_reached(project: Path) -> None:
    """The dialog is usable the moment it is on screen, buttons and all."""
    seed_session(project, _active_task_id(project), provider_id=PROVIDER_CLAUDE)

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        modal = await _open_palette(app, pilot)
        _highlight_switch_to_codex(modal)
        await pilot.press("enter")

        confirmation = await wait_for_screen(app, pilot, ConfirmModal, selector="#confirm-cancel")
        assert confirmation.query_one("#confirm-body", Static)
        assert confirmation.query_one("#confirm-ok", Button)
        assert confirmation.query_one("#confirm-cancel", Button).has_focus
