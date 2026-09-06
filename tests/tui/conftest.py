"""Deterministic fixtures for the headless dashboard tests.

Every provider probe, executable lookup, and provider launch is replaced with a
deterministic double: these tests never touch a real coding agent, never open a network
connection, and never consume model quota.
"""

import shutil
import subprocess
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any, cast

import pytest
from rich.console import Console
from textual.pilot import Pilot
from textual.screen import Screen
from textual.widgets import Static
from textual.worker import WorkerState

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.doctor import DoctorService
from cortexshift.application.handoff_builder import HandoffBuilder
from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.switch_service import SwitchService
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.domain.doctor import (
    AuthenticationStatus,
    DoctorReport,
    PlatformInfo,
    ProviderDiagnostic,
)
from cortexshift.domain.handoff import HandoffRecord, HandoffStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import (
    PROVIDER_ANTIGRAVITY,
    PROVIDER_CLAUDE,
    PROVIDER_CODEX,
    ProviderCapabilities,
    ProviderId,
)
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.tui.actions import TuiExitRequest
from cortexshift.tui.app import CortexShiftApp
from cortexshift.tui.facade import TuiFacade
from cortexshift.tui.models import TuiRepositoryModel, TuiStateSnapshot, TuiTaskModel
from cortexshift.tui.screens import TuiSection
from tests.cli_runner import PROVIDER_EXECUTABLES, path_without_providers

# The dashboard's Pilot is parameterised by the app's return type: an exit request, or
# nothing when the operator simply quit.
TuiPilot = Pilot[TuiExitRequest | None]

INSTALLED_PROVIDERS = {"claude": "/usr/local/bin/claude", "codex": "/usr/local/bin/codex"}

# How the waits below pace themselves. The poll interval only decides how promptly a
# satisfied condition is noticed; the timeout only decides how a genuine failure is
# reported. Neither is a wait for a race to resolve -- every wait has a condition.
WORKER_POLL_SECONDS = 0.01
WORKER_TIMEOUT_SECONDS = 15.0
_WORKER_ACTIVE_STATES = (WorkerState.PENDING, WorkerState.RUNNING)


@pytest.fixture
def provider_free_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every provider CLI from `PATH` for one test, keeping Git and Python.

    A GitHub runner has no coding agent installed, and deliberately so. Reproducing that
    here means a test that quietly leans on the developer's own install fails on the
    laptop, where it is cheap to notice, instead of only in CI.
    """
    monkeypatch.setenv("PATH", path_without_providers())
    for name in PROVIDER_EXECUTABLES:
        assert shutil.which(name) is None, f"{name} is still on PATH: the fixture is ineffective"


def run_git(args: list[str], cwd: Path) -> None:
    """Run a git command in an isolated temporary repository."""
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def make_git_repo(root: Path, *, dirty: bool = True) -> None:
    """Create a temporary Git repository, optionally with a dirty working tree."""
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["config", "user.email", "tester@cortexshift.invalid"], root)
    run_git(["config", "user.name", "CortexShift Tester"], root)
    (root / "src.py").write_text("print('hello')\n", encoding="utf-8")
    run_git(["add", "src.py"], root)
    run_git(["commit", "-qm", "initial commit"], root)
    if dirty:
        (root / "src.py").write_text("print('hello')\nprint('changed')\n", encoding="utf-8")
        (root / "untracked.py").write_text("# new\n", encoding="utf-8")


class FakeDoctorService(DoctorService):
    """Deterministic provider discovery that probes nothing."""

    def __init__(self, installed: dict[str, str] | None = None) -> None:
        self._installed = INSTALLED_PROVIDERS if installed is None else installed
        self.calls = 0

    def run_diagnostics(
        self,
        provider_ids: list[ProviderId] | None = None,
    ) -> DoctorReport:
        """Return a fixed report for the three supported providers."""
        self.calls += 1
        specs = [
            (PROVIDER_CLAUDE, "Claude Code", "claude", "2.1.0"),
            (PROVIDER_CODEX, "Codex", "codex", "0.44.0"),
            (PROVIDER_ANTIGRAVITY, "Antigravity", "agy", "1.2.0"),
        ]
        diagnostics = [
            ProviderDiagnostic(
                provider_id=provider_id,
                display_name=display_name,
                executable=executable,
                installed=executable in self._installed,
                resolved_path=self._installed.get(executable),
                version=version if executable in self._installed else None,
                authentication_status=(
                    AuthenticationStatus.AUTHENTICATED
                    if executable in self._installed
                    else AuthenticationStatus.UNKNOWN
                ),
                capabilities=ProviderCapabilities(
                    provider_id=provider_id,
                    display_name=display_name,
                    supports_native_resume=True,
                ),
                diagnostics=[] if executable in self._installed else ["Not found in PATH"],
            )
            for provider_id, display_name, executable, version in specs
        ]
        return DoctorReport(
            cortexshift_version="0.1.0",
            python_version="3.12.0",
            platform=PlatformInfo(
                system="TestOS", release="1.0", machine="x86_64", python_version="3.12.0"
            ),
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
            providers=diagnostics,
        )


def fake_which(installed: dict[str, str] | None = None) -> Callable[[str], str | None]:
    """Build a deterministic executable resolver."""
    resolved = INSTALLED_PROVIDERS if installed is None else installed

    def which(command: str) -> str | None:
        return resolved.get(command)

    return which


def seed_project(
    root: Path,
    *,
    name: str = "Luna",
    title: str = "Implement screen understanding",
    objective: str = "Add screen understanding to the agent loop.",
    requirements: list[str] | None = None,
    constraints: list[str] | None = None,
    completed: list[str] | None = None,
    remaining: list[str] | None = None,
    issues: list[str] | None = None,
    current_work: str | None = "Provider-specific runtime integration",
) -> Task:
    """Initialize a CortexShift project with one populated, active task."""
    ProjectInitializationService().initialize(root, name=name)
    workspace = TaskWorkspaceService()
    task = workspace.start_task(
        title=title,
        objective=objective,
        requirements=requirements if requirements is not None else ["Support multiple monitors"],
        constraints=constraints if constraints is not None else ["No new runtime dependencies"],
        start_dir=root,
    )
    if completed:
        workspace.mark_completed(completed, start_dir=root)
    if remaining:
        workspace.add_remaining(remaining, start_dir=root)
    if issues:
        workspace.record_issues(issues, start_dir=root)
    if current_work:
        workspace.set_current_work(current_work, start_dir=root)
    return workspace.get_active_task(root) or task


def seed_session(
    root: Path,
    task_id: str,
    *,
    provider_id: ProviderId = PROVIDER_CLAUDE,
    native_session_id: str | None = None,
    status: SessionStatus = SessionStatus.COMPLETED,
    exit_reason: SessionExitReason | None = SessionExitReason.NORMAL_COMPLETION,
    exit_code: int | None = 0,
) -> Session:
    """Persist a historical CortexShift session record."""
    session = Session(
        task_id=task_id,
        provider_id=provider_id,
        native_session_id=native_session_id,
        status=status,
        ended_at=utc_now() if status != SessionStatus.RUNNING else None,
        exit_reason=exit_reason,
        exit_code=exit_code,
    )
    with SQLiteStateStore(root / ".cortexshift" / "state.sqlite3", auto_migrate=False) as store:
        store.save_session(session)
    return session


def seed_handoff(
    root: Path,
    *,
    target_provider_id: ProviderId = PROVIDER_CODEX,
) -> HandoffRecord:
    """Persist a delivered handoff derived from canonical state, with no provider call."""
    db_path = root / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path, auto_migrate=False) as store:
        project = store.get_default_project()
        assert project is not None
        task_id = store.get_active_task_id(project.id)
        assert task_id is not None
        task = store.get_task(task_id)
        assert task is not None

        sessions = store.list_sessions(task_id=task.id, limit=1)
        source = sessions[0] if sessions else None
        if source is None:
            source = Session(
                task_id=task.id,
                provider_id=PROVIDER_CLAUDE,
                status=SessionStatus.COMPLETED,
                ended_at=utc_now(),
                exit_reason=SessionExitReason.NORMAL_COMPLETION,
                exit_code=0,
            )
            store.save_session(source)

        inspection = GitRepositoryInspector().inspect(project_root=root, project_id=project.id)
        payload = HandoffBuilder().build(
            project=project,
            task=task,
            source_session=source,
            inspection=inspection,
            target_provider_id=target_provider_id,
            snapshot_id=None,
            latest_checkpoint=store.get_latest_checkpoint(task.id),
        )
        record = HandoffRecord(
            project_id=project.id,
            task_id=task.id,
            source_session_id=source.id,
            source_provider_id=source.provider_id,
            target_provider_id=target_provider_id,
            status=HandoffStatus.DELIVERED,
            delivered_at=utc_now(),
            payload=payload,
        )
        store.save_handoff(record)
        return record


class NoLaunchProcessRunner:
    """An interactive runner that proves nothing is ever launched.

    Every dashboard path these tests exercise is a preview or a dry run, so reaching a
    real process would itself be the defect. Failing loudly beats spawning a provider.
    """

    def run_interactive(
        self,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        raise AssertionError(f"a provider process was launched by a test: {argv!r}")


def build_facade(root: Path, **overrides: object) -> TuiFacade:
    """Build a facade bound to a project with deterministic provider discovery.

    Provider presence comes entirely from fakes. The dashboard resolves provider
    executables in two independent places -- its own `which_fn`, used to decide which
    palette actions are offered, and the `SwitchService` behind `preview_switch`, whose
    dry run legitimately requires the target to be installed. Only the first was faked,
    so `preview_switch` fell through to the real `PATH`: the switch confirmation appeared
    on a machine that happened to have Codex installed and not on a CI runner, which has
    no provider CLI at all. Both now share one resolver.
    """
    which = cast(
        Callable[[str], str | None],
        overrides.pop("which_fn", None) or fake_which(),
    )
    kwargs: dict[str, object] = {
        "doctor_service": FakeDoctorService(),
        "which_fn": which,
        "switch_service": SwitchService(
            which_fn=which,
            process_runner=NoLaunchProcessRunner(),
        ),
        "provider_cache_seconds": 0.0,
    }
    kwargs.update(overrides)
    return TuiFacade(root, **kwargs)  # type: ignore[arg-type]


def build_app(root: Path, *, refresh_seconds: float = 60.0, **overrides: object) -> CortexShiftApp:
    """Build the dashboard bound to a project, with a quiet refresh timer by default."""
    return CortexShiftApp(build_facade(root, **overrides), state_refresh_seconds=refresh_seconds)


def widget_text(app: CortexShiftApp, selector: str, width: int = 200) -> str:
    """Render a widget's content to plain text.

    Rendering at a fixed generous width keeps assertions about content independent of
    the terminal size the test happens to use.
    """
    widget = app.screen.query_one(selector, Static)
    buffer = StringIO()
    console = Console(file=buffer, width=width, color_system=None, legacy_windows=False)
    console.print(widget.content)
    return buffer.getvalue()


def screen_text(app: CortexShiftApp, size: tuple[int, int]) -> str:
    """Render the whole composited screen, exactly as a terminal would show it."""
    buffer = StringIO()
    console = Console(
        file=buffer, width=size[0], height=size[1], color_system=None, legacy_windows=False
    )
    console.print(app.screen._compositor)
    return buffer.getvalue()


async def drain_workers(
    app: CortexShiftApp, pilot: TuiPilot, *, timeout: float = WORKER_TIMEOUT_SECONDS
) -> None:
    """Let every worker scheduled so far reach a terminal state.

    Deliberately not `WorkerManager.wait_for_complete()`. The state, repository, and
    provider refreshes are `exclusive` workers: one still in flight when the next starts
    is cancelled *by design*, and the app's generation guard drops its result. Awaiting
    such a worker raises `WorkerCancelled` for an outcome the application intends -- the
    dashboard is fine, only the wait is wrong. Waiting for the work to finish, rather
    than for a result no caller needed, states what the tests actually mean.

    Workers started while draining are picked up by the next `settle` round, exactly as
    they were before.
    """
    scheduled = list(app.workers)
    deadline = time.monotonic() + timeout
    while unfinished := [w for w in scheduled if w.state in _WORKER_ACTIVE_STATES]:
        if time.monotonic() > deadline:
            raise AssertionError(
                f"workers did not finish within {timeout:g}s: "
                + ", ".join(f"{w.name}={w.state.name}" for w in unfinished)
            )
        await pilot.pause(WORKER_POLL_SECONDS)


async def settle(app: CortexShiftApp, pilot: TuiPilot, rounds: int = 3) -> None:
    """Let pending workers finish and the resulting UI updates apply."""
    for _ in range(rounds):
        await pilot.pause()
        await drain_workers(app, pilot)
        await pilot.pause()


async def wait_until(
    pilot: TuiPilot,
    predicate: Callable[[], bool],
    *,
    description: str,
    timeout: float = WORKER_TIMEOUT_SECONDS,
) -> None:
    """Pump the event loop until `predicate` holds.

    Every wait in these tests has a condition: the loop ends the moment the application
    reaches the state under test, so a fast machine is never made to wait and a slow one
    is never cut short. The timeout only decides how a genuine failure is reported.
    """
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out after {timeout:g}s waiting until {description}")
        await pilot.pause(WORKER_POLL_SECONDS)


async def wait_for_state(
    app: CortexShiftApp,
    pilot: TuiPilot,
    accept: Callable[[TuiStateSnapshot], bool],
    *,
    since: datetime,
    description: str,
    timeout: float = WORKER_TIMEOUT_SECONDS,
) -> TuiStateSnapshot:
    """Wait for the dashboard's own refresh to publish state that `accept` approves.

    For tests where nothing tells the dashboard to reload: its timer has to surface the
    change on its own. The wait is on the published snapshot -- the app's observable
    state -- and never on a refresh worker, because the tick that finally reads the new
    data may cancel whichever worker the test happens to be holding.

    `since` makes the result authoritative: a snapshot assembled before that instant
    cannot end the wait even if it satisfies `accept`, so a stale read can never be
    mistaken for the refreshed one.
    """

    def published() -> bool:
        snapshot = app._snapshot
        if snapshot is None or snapshot.loaded_at is None or snapshot.loaded_at < since:
            return False
        return accept(snapshot)

    await wait_until(pilot, published, description=f"the dashboard {description}", timeout=timeout)
    snapshot = app._snapshot
    assert snapshot is not None
    return snapshot


def state(app: CortexShiftApp) -> TuiStateSnapshot:
    """Return the persisted state the dashboard has currently loaded."""
    snapshot = app._snapshot
    assert snapshot is not None, "the dashboard has not loaded any state yet"
    return snapshot


def active_task(app: CortexShiftApp) -> TuiTaskModel:
    """Return the active task as the dashboard currently shows it."""
    task = state(app).active_task
    assert task is not None, "the dashboard shows no active task"
    return task


def repository(app: CortexShiftApp) -> TuiRepositoryModel:
    """Return the most recent repository inspection the dashboard applied."""
    result = app._repository
    assert result is not None, "the dashboard has not inspected the repository yet"
    return result


def exit_request(app: CortexShiftApp) -> TuiExitRequest:
    """Return the provider launch request the dashboard exited with."""
    request = app.return_value
    assert isinstance(request, TuiExitRequest), f"expected an exit request, got {request!r}"
    return request


def assert_no_exit_request(app: CortexShiftApp) -> None:
    """Assert the dashboard has not asked for any provider launch."""
    assert app.return_value is None, f"unexpected provider launch request: {app.return_value!r}"


async def wait_for_screen[ScreenT: Screen[Any]](
    app: CortexShiftApp,
    pilot: TuiPilot,
    screen_type: type[ScreenT],
    *,
    timeout: float = WORKER_TIMEOUT_SECONDS,
) -> ScreenT:
    """Wait for a screen to be pushed, then assert it and return it.

    A modal built from a service result arrives in two hops: a worker thread computes
    the result, then the completion callback pushes the screen. Waiting for the screen
    to be mounted -- rather than for a number of settle rounds and hoping both hops fit
    inside them -- is what makes the assertion independent of how fast the machine is.

    The assertion is not weakened: the wait ends only when a screen of exactly this type
    is on top, and a failure still reports which screen was actually there.
    """
    await wait_until(
        pilot,
        lambda: isinstance(app.screen, screen_type),
        description=f"the {screen_type.__name__} is mounted (saw {type(app.screen).__name__})",
        timeout=timeout,
    )
    return assert_screen(app, screen_type)


def assert_screen[ScreenT: Screen[Any]](app: CortexShiftApp, screen_type: type[ScreenT]) -> ScreenT:
    """Assert which screen is on top, and return it."""
    screen = app.screen
    assert isinstance(screen, screen_type), (
        f"expected {screen_type.__name__}, saw {type(screen).__name__}"
    )
    return screen


def assert_base_screen(app: CortexShiftApp) -> None:
    """Assert no modal is open."""
    assert app.screen is app.screen_stack[0], f"unexpected modal: {type(app.screen).__name__}"


def assert_section(app: CortexShiftApp, expected: TuiSection) -> None:
    """Assert the visible section without narrowing it for the rest of the test."""
    assert app.active_section is expected, (
        f"expected the {expected.label} section, saw {app.active_section.label}"
    )


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Path]:
    """An initialized CortexShift project inside a dirty Git repository."""
    root = tmp_path / "workspace"
    root.mkdir()
    make_git_repo(root)
    seed_project(
        root,
        completed=["Scaffolded the capture module"],
        remaining=["Wire the parser", "Add regression tests"],
        issues=["Retina scaling is inconsistent"],
    )
    yield root


@pytest.fixture
def bare_project(tmp_path: Path) -> Iterator[Path]:
    """An initialized project with no task, no history, and no Git repository."""
    root = tmp_path / "bare"
    root.mkdir()
    ProjectInitializationService().initialize(root, name="Bare")
    yield root
