"""The terminal handoff flagship: Textual must finish before a provider starts.

This test drives the real Textual application to an exit request, then lets the real
coordinator dispatch it to a fake provider service. The assertion that matters is
ordering: the provider launch is observed strictly after the application's run loop has
finished, so no provider ever shares the terminal with the dashboard.
"""

from pathlib import Path

import pytest
from textual.widgets import OptionList

from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.tui.actions import TuiExitAction, TuiExitRequest
from cortexshift.tui.coordinator import TuiCoordinator
from cortexshift.tui.modals import ConfirmModal, ProviderActionModal
from tests.tui.conftest import (
    assert_screen,
    build_app,
    make_git_repo,
    seed_project,
    seed_session,
    settle,
    wait_for_screen,
)


class FakeNativeProvider:
    """A stand-in for the native provider process.

    Records whether the Textual application was still alive at launch time, which is the
    condition the architecture forbids.
    """

    def __init__(self) -> None:
        self.launches: list[str] = []
        self.app_alive_at_launch: list[bool] = []
        self.app: object | None = None

    def _observe(self, provider: str) -> Session:
        self.launches.append(provider)
        self.app_alive_at_launch.append(bool(getattr(self.app, "is_running", False)))
        return Session(
            task_id="task_x",
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.COMPLETED,
            ended_at=utc_now(),
            exit_reason=SessionExitReason.NORMAL_COMPLETION,
            exit_code=0,
        )

    def switch(self, target_provider_name: str, **kwargs: object) -> object:
        session = self._observe(target_provider_name)

        class Result:
            target_session = session

        return Result()

    def run(self, provider_name: str, **kwargs: object) -> Session:
        return self._observe(provider_name)

    def resume(self, provider_name: str, session_id: str | None = None, **kw: object) -> Session:
        return self._observe(provider_name)


@pytest.mark.asyncio
async def test_the_provider_launches_only_after_the_dashboard_run_loop_ends(
    tmp_path: Path,
) -> None:
    root = tmp_path / "handoff"
    root.mkdir()
    make_git_repo(root)
    task = seed_project(root, remaining=["Finish the switch"])
    seed_session(root, task.id, provider_id=PROVIDER_CLAUDE, native_session_id="claude-uuid-1")

    provider = FakeNativeProvider()
    timeline: list[str] = []

    # Drive the real Textual application to an exit request.
    app = build_app(root)
    provider.app = app

    async def drive() -> TuiExitRequest | None:
        timeline.append("dashboard_started")
        async with app.run_test(size=(110, 40)) as pilot:
            await settle(app, pilot)

            await pilot.press("x")
            await settle(app, pilot, rounds=4)
            palette = assert_screen(app, ProviderActionModal)

            index = next(
                i
                for i, option in enumerate(palette._options)
                if option.action is TuiExitAction.SWITCH and option.provider == "codex"
            )
            assert palette._options[index].enabled is True
            palette.query_one("#provider-actions", OptionList).highlighted = index
            await pilot.pause()
            await pilot.press("enter")

            await wait_for_screen(app, pilot, ConfirmModal)
            # Nothing launched while the dashboard is alive.
            assert provider.launches == []

            await pilot.press("tab")
            await pilot.press("enter")
            await pilot.pause()

        timeline.append("dashboard_returned")
        assert not app.is_running
        return app.return_value

    request = await drive()

    assert isinstance(request, TuiExitRequest)
    assert request.action is TuiExitAction.SWITCH
    assert request.provider == "codex"
    # Still nothing launched: the request is inert until the coordinator dispatches it.
    assert provider.launches == []

    # Only now does the coordinator hand the terminal over.
    coordinator = TuiCoordinator(
        run_service=provider,  # type: ignore[arg-type]
        resume_service=provider,  # type: ignore[arg-type]
        switch_service=provider,  # type: ignore[arg-type]
        dashboard_runner=lambda: request,
        announce=timeline.append,
    )
    result = coordinator.start(root)
    timeline.append("provider_launch_finished")

    assert provider.launches == ["codex"]
    # The Textual application was already finished when the provider was invoked.
    assert provider.app_alive_at_launch == [False]

    assert timeline.index("dashboard_returned") < timeline.index("provider_launch_finished")
    assert result.events == [
        "dashboard_finished",
        "provider_launch_started",
        "provider_launch_finished",
    ]
    assert result.session is not None
    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_quitting_the_dashboard_launches_no_provider(tmp_path: Path) -> None:
    root = tmp_path / "quit"
    root.mkdir()
    make_git_repo(root)
    seed_project(root, remaining=["Nothing to do"])

    provider = FakeNativeProvider()
    app = build_app(root)
    provider.app = app

    async with app.run_test(size=(100, 30)) as pilot:
        await settle(app, pilot)
        await pilot.press("q")
        await pilot.pause()

    coordinator = TuiCoordinator(
        run_service=provider,  # type: ignore[arg-type]
        resume_service=provider,  # type: ignore[arg-type]
        switch_service=provider,  # type: ignore[arg-type]
        dashboard_runner=lambda: app.return_value,
    )
    result = coordinator.start(root)

    assert app.return_value is None
    assert provider.launches == []
    assert result.launched is False
    assert result.exit_code == 0


def test_no_pty_or_terminal_emulator_dependency_is_present() -> None:
    """Provider terminals are owned by the provider; CortexShift never emulates one."""
    import importlib.metadata

    requirements = importlib.metadata.requires("cortexshift") or []
    names = {
        requirement.split()[0].split(";")[0].split("[")[0].lower() for requirement in requirements
    }

    for forbidden in ("pexpect", "ptyprocess", "pyte", "libtmux", "pyte3", "terminado"):
        assert forbidden not in names, f"{forbidden} must not be a CortexShift dependency"

    # And the TUI package itself imports no PTY machinery.
    tui_root = Path(__file__).resolve().parents[2] / "src" / "cortexshift" / "tui"
    sources = "\n".join(path.read_text(encoding="utf-8") for path in tui_root.rglob("*.py"))
    for forbidden_import in ("import pty", "import pexpect", "import ptyprocess", "import pyte"):
        assert forbidden_import not in sources
