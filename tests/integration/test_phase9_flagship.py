"""Flagship Phase 9 workflow: one project, driven end to end through the dashboard.

Everything below runs headlessly against a real temporary project and a real temporary
Git repository. Provider discovery is a deterministic double, and no provider process is
ever launched — the point of the final step is precisely that the dashboard *stops*
before a provider would start.
"""

from pathlib import Path

import pytest
from textual.widgets import DataTable, Input, OptionList, Static

from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.session_service import SessionService
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.domain.checkpoint import CheckpointKind, CheckpointTestProvenance
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import SessionExitReason, SessionStatus
from cortexshift.tui.actions import TuiExitAction
from cortexshift.tui.modals import ConfirmModal, ProviderActionModal
from cortexshift.tui.screens import TuiSection
from cortexshift.tui.screens.sessions import SessionsSection
from tests.tui.conftest import (
    assert_no_exit_request,
    assert_screen,
    assert_section,
    build_app,
    exit_request,
    make_git_repo,
    screen_text,
    seed_handoff,
    seed_project,
    seed_session,
    settle,
    wait_for_screen,
    widget_text,
)

SIZE = (110, 40)


@pytest.mark.asyncio
async def test_phase9_flagship_control_center_workflow(tmp_path: Path) -> None:
    # 1. A temporary CortexShift project inside a real, dirty Git repository.
    root = tmp_path / "flagship"
    root.mkdir()
    make_git_repo(root, dirty=True)

    # 2. An active Task with a full canonical shape.
    task = seed_project(
        root,
        name="Luna",
        title="Implement screen understanding",
        objective="Give the agent loop a reliable view of the screen.",
        requirements=["Support multiple monitors", "Never capture credentials"],
        constraints=["No new runtime dependencies"],
        completed=["Scaffolded the capture module"],
        remaining=["Wire the parser", "Add regression tests"],
        issues=["Retina scaling is inconsistent"],
        current_work="Provider-specific runtime integration",
    )

    # 3. Session history: an earlier, exactly resumable Codex conversation, then the
    #    Claude session that did the most recent work. Switching back to Codex should
    #    therefore reuse the known native conversation rather than starting a fresh one.
    resumable_codex = seed_session(
        root,
        task.id,
        provider_id=PROVIDER_CODEX,
        native_session_id="codex-thread-1",
        status=SessionStatus.INTERRUPTED,
        exit_reason=SessionExitReason.USER_INTERRUPTED,
        exit_code=130,
    )
    seed_session(root, task.id, provider_id=PROVIDER_CLAUDE, native_session_id="claude-uuid-1")

    # 4. Checkpoint and handoff history.
    manual = CheckpointService().create_checkpoint(
        decisions=["Chose exact native resume over transcript replay"],
        test_summary="572 passed",
        note="Milestone before the control center landed",
        start_dir=root,
    )
    assert manual.kind is CheckpointKind.MANUAL
    handoff = seed_handoff(root)

    # 5. Launch the dashboard headlessly.
    app = build_app(root)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)

        # 6. Overview shows project, task, progress, repository, activity, providers.
        assert_section(app, TuiSection.OVERVIEW)
        overview = screen_text(app, SIZE)
        assert "Luna" in overview
        assert "Implement screen understanding" in overview

        assert "1/3" in widget_text(app, "#overview-progress")
        assert "main" in widget_text(app, "#overview-repository")
        assert "dirty" in widget_text(app, "#overview-repository")

        activity = widget_text(app, "#overview-activity")
        assert "claude" in activity  # newest session
        assert "manual" in activity  # newest checkpoint
        assert "delivered" in activity  # newest handoff

        providers = widget_text(app, "#overview-providers")
        assert "Claude Code" in providers
        assert "Antigravity" in providers

        # 7. Navigate to the Task screen.
        await pilot.press("2")
        await settle(app, pilot)
        assert_section(app, TuiSection.TASK)
        assert task.id in widget_text(app, "#task-identity")
        assert "Never capture credentials" in widget_text(app, "#task-requirements")

        # 8. Update current work through the modal.
        await pilot.press("w")
        await pilot.pause()
        app.screen.query_one("#entry-input", Input).value = "Wiring the control center"
        await pilot.press("enter")
        await settle(app, pilot)

        updated = TaskWorkspaceService().get_active_task(root)
        assert updated is not None
        assert updated.current_work == "Wiring the control center"
        assert "Wiring the control center" in widget_text(app, "#task-current-work")

        # 9. Mark one remaining item completed — the Task itself stays in progress.
        await pilot.press("m")
        await pilot.pause()
        app.screen.query_one("#entry-input", Input).value = "Wire the parser"
        await pilot.press("enter")
        await settle(app, pilot)

        updated = TaskWorkspaceService().get_active_task(root)
        assert updated is not None
        assert "Wire the parser" in updated.completed
        assert "Wire the parser" not in updated.remaining
        assert updated.remaining == ["Add regression tests"]
        assert updated.status.value == "in_progress"
        assert "2/3" in widget_text(app, "#task-progress")

        # 10. Create a MANUAL checkpoint through the dashboard.
        await pilot.press("c")
        await settle(app, pilot)
        app.screen.query_one("#checkpoint-decision", Input).value = "Textual for the control center"
        app.screen.query_one("#checkpoint-tests", Input).value = "600 passed"
        app.screen.query_one("#checkpoint-note", Input).value = "Captured from the dashboard"
        await pilot.press("enter")
        await settle(app, pilot)

        checkpoints = CheckpointService().list_checkpoints(start_dir=root)
        assert len(checkpoints) == 2
        newest = checkpoints[0]
        assert newest.kind is CheckpointKind.MANUAL
        assert newest.payload.test_status.provenance is CheckpointTestProvenance.REPORTED

        # 11. Repository screen, then an explicit refresh.
        await pilot.press("3")
        await settle(app, pilot)
        assert_section(app, TuiSection.REPOSITORY)
        assert "main" in widget_text(app, "#repo-git")
        assert "untracked.py" in widget_text(app, "#repo-untracked")

        (root / "another.py").write_text("# added mid-session\n", encoding="utf-8")
        await pilot.press("r")
        await settle(app, pilot, rounds=4)
        assert "another.py" in widget_text(app, "#repo-untracked")

        # 12. Sessions screen: native resumability is visible and honest.
        await pilot.press("4")
        await settle(app, pilot)
        assert_section(app, TuiSection.SESSIONS)
        table = app.query_one("#sessions-table", DataTable)
        assert table.row_count == 2

        section = app.section_view(TuiSection.SESSIONS)
        assert isinstance(section, SessionsSection)
        table.move_cursor(row=table.get_row_index(resumable_codex.id), animate=False)
        await pilot.pause()
        detail = widget_text(app, "#sessions-detail")
        assert resumable_codex.id in detail
        assert "codex-thread-1" in detail
        assert "Native resumable" in detail
        assert section.selected_session is not None
        assert section.selected_session.native_resumable is True

        # 13. Checkpoints screen: the new checkpoint is there and labelled honestly.
        await pilot.press("5")
        await settle(app, pilot)
        assert_section(app, TuiSection.CHECKPOINTS)
        cp_table = app.query_one("#checkpoints-table", DataTable)
        assert cp_table.row_count == 2
        cp_table.move_cursor(row=cp_table.get_row_index(newest.id), animate=False)
        await pilot.pause()

        cp_detail = widget_text(app, "#checkpoints-detail")
        assert newest.id in cp_detail
        assert "600 passed" in cp_detail
        assert "Reported / unverified" in cp_detail
        assert "Historical observation" in cp_detail

        # 14. Handoffs screen.
        await pilot.press("6")
        await settle(app, pilot)
        assert_section(app, TuiSection.HANDOFFS)
        assert app.query_one("#handoffs-table", DataTable).row_count == 1
        assert handoff.id in widget_text(app, "#handoffs-detail")

        # 15. Providers screen.
        await pilot.press("7")
        await settle(app, pilot)
        assert_section(app, TuiSection.PROVIDERS)
        assert app.query_one("#providers-table", DataTable).row_count == 3
        assert "stdio" in widget_text(app, "#providers-mcp")

        # 16. Choose Switch → Codex.
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

        confirm = await wait_for_screen(app, pilot, ConfirmModal)
        preview = str(confirm.query_one("#confirm-body", Static).content)
        assert "Codex" in preview
        assert "resume_existing" in preview
        assert "Nothing has been persisted or launched yet" in preview

        # 18. No provider was launched while the Textual app is still alive.
        assert app.is_running
        assert_no_exit_request(app)
        assert SessionService().list_sessions(root) is not None
        assert len(SessionService().list_sessions(root)) == 2

        await pilot.press("tab")
        await pilot.press("enter")
        await pilot.pause()

    # 17. Textual returned a structured exit request instead of launching anything.
    request = exit_request(app)
    assert request.action is TuiExitAction.SWITCH
    assert request.provider == "codex"

    # Still exactly two sessions: the switch has not run.
    assert len(SessionService().list_sessions(root)) == 2

    # Canonical state written through the dashboard survived the app exiting.
    final = TaskWorkspaceService().get_active_task(root)
    assert final is not None
    assert final.current_work == "Wiring the control center"
    assert "Wire the parser" in final.completed
    assert len(CheckpointService().list_checkpoints(start_dir=root)) == 2


@pytest.mark.asyncio
async def test_phase9_flagship_leaves_the_schema_at_v6(tmp_path: Path) -> None:
    """The dashboard is ephemeral: it stores no UI state and needs no migration."""
    from cortexshift.adapters.sqlite.migrations import CURRENT_SCHEMA_VERSION
    from cortexshift.adapters.sqlite.store import SQLiteStateStore

    root = tmp_path / "schema"
    root.mkdir()
    make_git_repo(root)
    seed_project(root, remaining=["An item"])

    db_path = root / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path, auto_migrate=False) as store:
        before = store.get_schema_version()
        tables_before = {
            row[0]
            for row in store._conn.execute(  # noqa: SLF001 - test-only introspection
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    app = build_app(root)
    async with app.run_test(size=SIZE) as pilot:
        await settle(app, pilot)
        for section in TuiSection:
            await pilot.press(section.shortcut)
            await settle(app, pilot)

        await pilot.press("c")
        await settle(app, pilot)
        app.screen.query_one("#checkpoint-note", Input).value = "Schema check"
        await pilot.press("enter")
        await settle(app, pilot)

    with SQLiteStateStore(db_path, auto_migrate=False) as store:
        after = store.get_schema_version()
        tables_after = {
            row[0]
            for row in store._conn.execute(  # noqa: SLF001 - test-only introspection
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

    assert before == after == CURRENT_SCHEMA_VERSION == 6
    assert tables_before == tables_after
    # No UI preference table was introduced.
    assert not any("ui" in name or "tui" in name for name in tables_after)
