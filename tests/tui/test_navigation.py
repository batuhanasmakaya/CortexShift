"""Keyboard navigation, layout responsiveness, and help."""

import asyncio
import threading
from pathlib import Path

import pytest

from cortexshift.tui.app import STATE_WORKER_GROUP
from cortexshift.tui.models import TuiStateSnapshot
from cortexshift.tui.screens import SECTION_ORDER, TuiSection
from cortexshift.tui.screens.help import HelpScreen
from tests.tui.conftest import (
    WORKER_TIMEOUT_SECONDS,
    assert_base_screen,
    assert_screen,
    assert_section,
    build_app,
    drain_workers,
    settle,
    state,
)

NAVIGATION_SEQUENCE = [
    ("2", TuiSection.TASK),
    ("3", TuiSection.REPOSITORY),
    ("4", TuiSection.SESSIONS),
    ("5", TuiSection.CHECKPOINTS),
    ("6", TuiSection.HANDOFFS),
    ("7", TuiSection.PROVIDERS),
    ("1", TuiSection.OVERVIEW),
]


@pytest.mark.asyncio
async def test_overview_is_the_default_section(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        assert_section(app, TuiSection.OVERVIEW)


@pytest.mark.asyncio
async def test_number_keys_navigate_every_primary_section(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        for key, expected in NAVIGATION_SEQUENCE:
            await pilot.press(key)
            await pilot.pause()
            assert_section(app, expected)


@pytest.mark.asyncio
async def test_every_section_is_mounted_and_addressable(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = build_app(project)
    load_state = app.facade.load_state
    loop = asyncio.get_running_loop()
    started = asyncio.Event()
    release = threading.Event()

    def read_state() -> TuiStateSnapshot:
        loop.call_soon_threadsafe(started.set)
        assert release.wait(WORKER_TIMEOUT_SECONDS), "the test did not release the state read"
        return load_state()

    monkeypatch.setattr(app.facade, "load_state", read_state)
    async with app.run_test():
        try:
            await asyncio.wait_for(started.wait(), timeout=WORKER_TIMEOUT_SECONDS)
            assert app._snapshot is None
            for section in SECTION_ORDER:
                view = app.section_view(section)
                assert view.section is section
        finally:
            release.set()
        await drain_workers(app, group=STATE_WORKER_GROUP)
        assert state(app).project.name == "Luna"


@pytest.mark.asyncio
async def test_sidebar_navigation_switches_sections(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        nav = app.query_one("#nav")
        nav.focus()
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert_section(app, TuiSection.TASK)


@pytest.mark.asyncio
async def test_help_opens_and_closes(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("question_mark")
        await pilot.pause()
        assert_screen(app, HelpScreen)

        await pilot.press("escape")
        await pilot.pause()
        assert_base_screen(app)
        assert_section(app, TuiSection.OVERVIEW)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (100, 30), (120, 40)])
async def test_size_matrix_keeps_every_section_reachable(
    project: Path, size: tuple[int, int]
) -> None:
    """The dashboard must stay usable from 80x24 upwards."""
    app = build_app(project)
    async with app.run_test(size=size) as pilot:
        await settle(app, pilot)
        for section in SECTION_ORDER:
            await pilot.press(section.shortcut)
            await settle(app, pilot)
            assert_section(app, section)
            assert app.section_view(section).display

        assert state(app).project.name == "Luna"


@pytest.mark.asyncio
async def test_narrow_terminals_fold_the_sidebar_away(project: Path) -> None:
    """Below 90 columns the sidebar folds; number keys still navigate."""
    app = build_app(project)
    async with app.run_test(size=(80, 24)) as pilot:
        await settle(app, pilot)
        assert app.has_class("compact")
        assert not app.query_one("#sidebar").display

        await pilot.press("4")
        await pilot.pause()
        assert_section(app, TuiSection.SESSIONS)


@pytest.mark.asyncio
async def test_wide_terminals_show_the_sidebar(project: Path) -> None:
    app = build_app(project)
    async with app.run_test(size=(120, 40)) as pilot:
        await settle(app, pilot)
        assert not app.has_class("compact")
        assert app.query_one("#sidebar").display


@pytest.mark.asyncio
async def test_unusably_small_terminal_shows_a_message_instead_of_crashing(project: Path) -> None:
    app = build_app(project)
    async with app.run_test(size=(40, 10)) as pilot:
        await settle(app, pilot)
        assert app.has_class("too-small")
        assert app.query_one("#too-small").display
        assert app.is_running
