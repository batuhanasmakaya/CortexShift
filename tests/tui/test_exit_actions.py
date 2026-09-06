"""The terminal handoff boundary: the dashboard exits, then a provider launches."""

from pathlib import Path

import pytest
from textual.widgets import OptionList

from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.tui.actions import TuiExitAction, TuiExitRequest
from cortexshift.tui.app import CortexShiftApp
from cortexshift.tui.modals import ConfirmModal, ProviderActionModal
from tests.tui.conftest import (
    TuiPilot,
    assert_no_exit_request,
    assert_screen,
    build_app,
    exit_request,
    seed_session,
    settle,
    wait_for_screen,
)


def _active_task_id(root: Path) -> str:
    from cortexshift.application.task_workspace import TaskWorkspaceService

    task = TaskWorkspaceService().get_active_task(root)
    assert task is not None
    return task.id


async def open_provider_palette(app: CortexShiftApp, pilot: TuiPilot) -> ProviderActionModal:
    """Open the provider action palette and return it."""
    await pilot.press("x")
    return await wait_for_screen(app, pilot, ProviderActionModal)


def highlight(modal: ProviderActionModal, action: TuiExitAction, provider: str) -> None:
    """Move the palette cursor onto one concrete provider action."""
    index = next(
        i
        for i, option in enumerate(modal._options)
        if option.action is action and option.provider == provider
    )
    modal.query_one("#provider-actions", OptionList).highlighted = index


@pytest.mark.asyncio
async def test_exit_request_rejects_contradictory_options() -> None:
    with pytest.raises(ValueError):
        TuiExitRequest(
            action=TuiExitAction.RESUME,
            provider="codex",
            selected_session_id="sess_1",
            force_new_session=True,
        )
    with pytest.raises(ValueError):
        TuiExitRequest(action=TuiExitAction.RUN, provider="codex", selected_session_id="sess_1")
    with pytest.raises(ValueError):
        TuiExitRequest(action=TuiExitAction.RUN, provider="   ")


@pytest.mark.asyncio
async def test_provider_palette_only_enables_valid_actions(project: Path) -> None:
    task_id = _active_task_id(project)
    seed_session(project, task_id, provider_id=PROVIDER_CLAUDE, native_session_id="uuid-a")

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        modal = await open_provider_palette(app, pilot)
        options = {(o.action, o.provider): o for o in modal._options}

        # Antigravity is not installed in this fixture; every action is disabled with a reason.
        for action in TuiExitAction:
            option = options[(action, "antigravity")]
            assert option.enabled is False
            assert "not found in PATH" in (option.disabled_reason or "")

        # Claude has exactly resumable history, so resume is offered with the session.
        resume_claude = options[(TuiExitAction.RESUME, "claude")]
        assert resume_claude.enabled is True
        assert resume_claude.selected_session_id is not None

        # Codex has no resumable history: disabled, with the reason stated.
        resume_codex = options[(TuiExitAction.RESUME, "codex")]
        assert resume_codex.enabled is False
        assert "exactly resumable" in (resume_codex.disabled_reason or "")

        # Switching to the provider that owns the latest session is refused up front.
        switch_claude = options[(TuiExitAction.SWITCH, "claude")]
        assert switch_claude.enabled is False
        assert "Resume instead" in (switch_claude.disabled_reason or "")

        assert options[(TuiExitAction.SWITCH, "codex")].enabled is True
        assert options[(TuiExitAction.RUN, "claude")].enabled is True


@pytest.mark.asyncio
async def test_switch_returns_an_exit_request_and_launches_nothing(project: Path) -> None:
    """Selecting Switch → Codex ends the app with a request; no provider runs."""
    seed_session(project, _active_task_id(project), provider_id=PROVIDER_CLAUDE)

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        modal = await open_provider_palette(app, pilot)

        highlight(modal, TuiExitAction.SWITCH, "codex")
        await pilot.pause()
        await pilot.press("enter")

        # A confirmation appears first, built from the switch dry run. Waiting for it to
        # be mounted proves it was actually pushed; a swallowed error would time out here.
        await wait_for_screen(app, pilot, ConfirmModal)
        # Still running: nothing has been launched and nothing has been persisted.
        assert app.is_running
        assert_no_exit_request(app)

        await pilot.press("tab")
        await pilot.press("enter")
        await pilot.pause()

    request = exit_request(app)
    assert request.action is TuiExitAction.SWITCH
    assert request.provider == "codex"
    assert request.force_new_session is False


@pytest.mark.asyncio
async def test_declining_the_switch_confirmation_keeps_the_dashboard_open(project: Path) -> None:
    seed_session(project, _active_task_id(project), provider_id=PROVIDER_CLAUDE)

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        modal = await open_provider_palette(app, pilot)
        highlight(modal, TuiExitAction.SWITCH, "codex")
        await pilot.pause()
        await pilot.press("enter")
        await wait_for_screen(app, pilot, ConfirmModal)

        await pilot.press("escape")
        await settle(app, pilot)

        assert app.is_running
        assert_no_exit_request(app)


@pytest.mark.asyncio
async def test_run_returns_an_exit_request_without_a_confirmation_step(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        modal = await open_provider_palette(app, pilot)
        highlight(modal, TuiExitAction.RUN, "claude")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    request = exit_request(app)
    assert request.action is TuiExitAction.RUN
    assert request.provider == "claude"
    assert request.selected_session_id is None


@pytest.mark.asyncio
async def test_resume_carries_the_selected_native_session(project: Path) -> None:
    task_id = _active_task_id(project)
    resumable = seed_session(
        project, task_id, provider_id=PROVIDER_CODEX, native_session_id="codex-thread-1"
    )

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        modal = await open_provider_palette(app, pilot)
        highlight(modal, TuiExitAction.RESUME, "codex")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    request = exit_request(app)
    assert request.action is TuiExitAction.RESUME
    assert request.selected_session_id == resumable.id


@pytest.mark.asyncio
async def test_cancelling_the_palette_returns_no_request(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await open_provider_palette(app, pilot)
        await pilot.press("escape")
        await settle(app, pilot)
        assert app.is_running
        assert_no_exit_request(app)


@pytest.mark.asyncio
async def test_quitting_normally_returns_no_provider_request(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("q")
        await pilot.pause()

    assert_no_exit_request(app)


@pytest.mark.asyncio
async def test_handoff_preview_persists_nothing_and_launches_nothing(project: Path) -> None:
    from cortexshift.application.handoff_service import HandoffService
    from cortexshift.tui.modals import InfoModal

    seed_session(project, _active_task_id(project), provider_id=PROVIDER_CLAUDE)

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("p")
        await settle(app, pilot)
        modal = assert_screen(app, ProviderActionModal)

        highlight(modal, TuiExitAction.SWITCH, "codex")
        await pilot.pause()
        await pilot.press("enter")

        await wait_for_screen(app, pilot, InfoModal)
        assert app.is_running
        assert_no_exit_request(app)

    # Nothing was written to the handoff history.
    assert HandoffService().list_handoffs(project) == []
