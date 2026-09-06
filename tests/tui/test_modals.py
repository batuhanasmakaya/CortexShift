"""Modal dialogs mutate canonical state only on explicit confirmation."""

from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, OptionList

from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.domain.checkpoint import CheckpointKind, CheckpointTestProvenance
from cortexshift.tui.modals import (
    AddRemainingModal,
    CheckpointModal,
    MarkCompletedModal,
    RecordIssueModal,
    SetCurrentWorkModal,
)
from cortexshift.tui.screens import TuiSection
from tests.tui.conftest import (
    assert_base_screen,
    assert_screen,
    build_app,
    settle,
    state,
    widget_text,
)


def active_task(root: Path) -> object:
    task = TaskWorkspaceService().get_active_task(root)
    assert task is not None
    return task


@pytest.mark.asyncio
async def test_set_current_work_modal_updates_canonical_task(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("w")
        await pilot.pause()
        modal = assert_screen(app, SetCurrentWorkModal)

        modal.query_one("#entry-input", Input).value = "Wiring the control center"
        await pilot.press("enter")
        await settle(app, pilot)

        assert active_task(project).current_work == "Wiring the control center"  # type: ignore[attr-defined]
        assert "Wiring the control center" in widget_text(app, "#overview-task")


@pytest.mark.asyncio
async def test_mark_completed_moves_the_item_without_completing_the_task(project: Path) -> None:
    """Matches MCP and domain semantics exactly: the Task itself stays in progress."""
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("m")
        await pilot.pause()
        modal = assert_screen(app, MarkCompletedModal)

        modal.query_one("#entry-input", Input).value = "Wire the parser"
        await pilot.press("enter")
        await settle(app, pilot)

        task = active_task(project)
        assert "Wire the parser" in task.completed  # type: ignore[attr-defined]
        assert "Wire the parser" not in task.remaining  # type: ignore[attr-defined]
        assert "Add regression tests" in task.remaining  # type: ignore[attr-defined]
        assert task.status.value == "in_progress"  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_mark_completed_modal_offers_the_remaining_items(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        await pilot.press("m")
        await pilot.pause()

        modal = assert_screen(app, MarkCompletedModal)
        options = modal.query_one("#completed-options", OptionList)
        assert options.option_count == 2
        # The first remaining item is pre-filled, so Enter alone is meaningful.
        assert modal.query_one("#entry-input", Input).value == "Wire the parser"


@pytest.mark.asyncio
async def test_add_remaining_modal_appends_an_item(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("n")
        await pilot.pause()
        modal = assert_screen(app, AddRemainingModal)

        modal.query_one("#entry-input", Input).value = "Publish the ADR"
        await pilot.press("enter")
        await settle(app, pilot)

        assert "Publish the ADR" in active_task(project).remaining  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_record_issue_modal_appends_an_issue(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("i")
        await pilot.pause()
        modal = assert_screen(app, RecordIssueModal)

        modal.query_one("#entry-input", Input).value = "Textual 8 is required"
        await pilot.press("enter")
        await settle(app, pilot)

        assert "Textual 8 is required" in active_task(project).known_issues  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_empty_input_is_rejected_and_nothing_is_mutated(project: Path) -> None:
    before = active_task(project)
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("n")
        await pilot.pause()
        modal = assert_screen(app, AddRemainingModal)

        await pilot.press("enter")
        await pilot.pause()

        # The dialog stays open with a visible error, and nothing was written.
        assert app.screen is modal
        assert "required" in widget_text(app, "#entry-error").lower()

    assert active_task(project).remaining == before.remaining  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_cancelling_a_modal_mutates_nothing(project: Path) -> None:
    before = active_task(project)
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("w")
        await pilot.pause()
        assert_screen(app, SetCurrentWorkModal).query_one(
            "#entry-input", Input
        ).value = "Should never be saved"
        await pilot.press("escape")
        await settle(app, pilot)

    assert active_task(project).current_work == before.current_work  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_confirming_an_empty_field_clears_current_work(project: Path) -> None:
    """Clearing is a confirmed action, and stays distinct from cancelling."""
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("w")
        await pilot.pause()
        assert_screen(app, SetCurrentWorkModal).query_one("#entry-input", Input).value = ""
        await pilot.press("enter")
        await settle(app, pilot)

    assert active_task(project).current_work is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_opening_the_checkpoint_modal_creates_nothing(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("c")
        await settle(app, pilot)
        assert_screen(app, CheckpointModal)

        assert CheckpointService().list_checkpoints(start_dir=project) == []

        await pilot.press("escape")
        await settle(app, pilot)

    assert CheckpointService().list_checkpoints(start_dir=project) == []


@pytest.mark.asyncio
async def test_checkpoint_modal_creates_a_manual_checkpoint_with_reported_tests(
    project: Path,
) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        await pilot.press("c")
        await settle(app, pilot)
        modal = assert_screen(app, CheckpointModal)

        modal.query_one("#checkpoint-decision", Input).value = "Textual selected for the TUI"
        modal.query_one("#checkpoint-tests", Input).value = "581 passed"
        modal.query_one("#checkpoint-note", Input).value = "Before the docs pass"
        await pilot.press("enter")
        await settle(app, pilot)

        checkpoints = CheckpointService().list_checkpoints(start_dir=project)
        assert len(checkpoints) == 1
        record = checkpoints[0]
        assert record.kind is CheckpointKind.MANUAL
        assert record.payload.decisions == ["Textual selected for the TUI"]
        assert record.payload.operator_note == "Before the docs pass"

        # Reported, never verified.
        assert record.payload.test_status.known is True
        assert record.payload.test_status.provenance is CheckpointTestProvenance.REPORTED

        # The screen refreshed without reopening the app.
        assert len(state(app).checkpoints) == 1

        app.show_section(TuiSection.CHECKPOINTS)
        await settle(app, pilot)
        assert "Reported / unverified" in widget_text(app, "#checkpoints-detail")


@pytest.mark.asyncio
async def test_task_mutations_without_an_active_task_are_refused_cleanly(
    bare_project: Path,
) -> None:
    app = build_app(bare_project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        for key in ("w", "m", "n", "i", "c"):
            await pilot.press(key)
            await pilot.pause()
            # No modal opens, and the dashboard stays alive.
            assert_base_screen(app)
            assert app.is_running


@pytest.mark.asyncio
async def test_activating_a_task_from_the_table(project: Path) -> None:
    workspace = TaskWorkspaceService()
    second = workspace.start_task(
        title="Second task",
        objective="Something else entirely",
        set_active=False,
        start_dir=project,
    )

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.show_section(TuiSection.TASK)
        await settle(app, pilot)

        table = app.query_one("#task-table", DataTable)
        row_index = table.get_row_index(second.id)
        table.move_cursor(row=row_index, animate=False)
        await pilot.pause()

        await pilot.press("a")
        await settle(app, pilot)

    active = workspace.get_active_task(project)
    assert active is not None
    assert active.id == second.id
