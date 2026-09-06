"""Each primary section renders real data, and states emptiness usefully."""

from pathlib import Path

import pytest
from textual.widgets import DataTable

from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import SessionExitReason, SessionStatus
from cortexshift.tui.screens import TuiSection
from cortexshift.tui.widgets import EmptyState
from tests.tui.conftest import assert_section, build_app, seed_session, settle, widget_text


def rendered(app: object, selector: str) -> str:
    """Return the plain text a widget currently renders."""
    return widget_text(app, selector)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_overview_shows_project_task_repository_and_activity(project: Path) -> None:
    seed_session(project, _active_task_id(project), native_session_id="native-1")
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        assert "Luna" in rendered(app, "#overview-project")
        assert "0.1.0" in rendered(app, "#overview-project")
        assert "v6" in rendered(app, "#overview-project")

        assert "Implement screen understanding" in rendered(app, "#overview-task-title")
        assert "Provider-specific runtime integration" in rendered(app, "#overview-task")

        # Structured progress: one completed, two remaining.
        assert "1/3" in rendered(app, "#overview-progress")

        assert "main" in rendered(app, "#overview-repository")
        assert "dirty" in rendered(app, "#overview-repository")

        assert "claude" in rendered(app, "#overview-activity")
        assert "Claude Code" in rendered(app, "#overview-providers")


@pytest.mark.asyncio
async def test_overview_progress_states_absence_rather_than_inventing_a_percentage(
    tmp_path: Path,
) -> None:
    """With no structured items there is no denominator, so no percentage is shown."""
    from tests.tui.conftest import seed_project

    root = tmp_path / "noprogress"
    root.mkdir()
    seed_project(root, completed=None, remaining=None, current_work=None)

    app = build_app(root)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        body = rendered(app, "#overview-progress")
        assert "No structured progress yet" in body
        assert "%" not in body


@pytest.mark.asyncio
async def test_task_screen_shows_complete_canonical_state(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.show_section(TuiSection.TASK)
        await settle(app, pilot)
        assert_section(app, TuiSection.TASK)

        identity = rendered(app, "#task-identity")
        assert app._snapshot is not None
        assert app._snapshot.active_task is not None
        # Detail views expose the full canonical ID, never an abbreviation.
        assert app._snapshot.active_task.id in identity

        assert "Add screen understanding" in rendered(app, "#task-objective")
        assert "Support multiple monitors" in rendered(app, "#task-requirements")
        assert "No new runtime dependencies" in rendered(app, "#task-constraints")
        assert "Scaffolded the capture module" in rendered(app, "#task-completed")
        assert "Wire the parser" in rendered(app, "#task-remaining")
        assert "Retina scaling is inconsistent" in rendered(app, "#task-issues")

        table = app.query_one("#task-table", DataTable)
        assert table.row_count == 1


@pytest.mark.asyncio
async def test_repository_screen_shows_live_git_state_without_diffs(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.show_section(TuiSection.REPOSITORY)
        await settle(app, pilot)

        git = rendered(app, "#repo-git")
        assert "main" in git
        assert "yes" in git  # dirty

        assert "untracked.py" in rendered(app, "#repo-untracked")
        assert "src.py" in rendered(app, "#repo-modified")

        # A shortstat summary is shown; the diff body never is.
        diff = rendered(app, "#repo-diff")
        assert "insertion" in diff or "—" in diff
        assert "print('changed')" not in diff


@pytest.mark.asyncio
async def test_sessions_screen_shows_native_resume_metadata(project: Path) -> None:
    task_id = _active_task_id(project)
    seed_session(project, task_id, provider_id=PROVIDER_CLAUDE, native_session_id="uuid-a")
    seed_session(
        project,
        task_id,
        provider_id=PROVIDER_CODEX,
        native_session_id=None,
        status=SessionStatus.FAILED,
        exit_reason=SessionExitReason.SPAWN_FAILED,
        exit_code=127,
    )

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.show_section(TuiSection.SESSIONS)
        await settle(app, pilot)

        table = app.query_one("#sessions-table", DataTable)
        assert table.row_count == 2

        rows = {row.provider_id: row for row in app._snapshot.sessions}  # type: ignore[union-attr]
        assert rows["claude"].native_resumable is True
        # A spawn failure is never exactly resumable, even if an ID were recorded.
        assert rows["codex"].native_resumable is False

        detail = rendered(app, "#sessions-detail")
        assert "Session ID" in detail


@pytest.mark.asyncio
async def test_checkpoints_screen_labels_history_and_unverified_tests(project: Path) -> None:
    CheckpointService().create_checkpoint(
        decisions=["Chose exact native resume"],
        test_summary="572 passed",
        note="Milestone",
        start_dir=project,
    )

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.show_section(TuiSection.CHECKPOINTS)
        await settle(app, pilot)

        assert app.query_one("#checkpoints-table", DataTable).row_count == 1

        detail = rendered(app, "#checkpoints-detail")
        assert "Historical observation" in detail
        assert "572 passed" in detail
        assert "Reported / unverified" in detail
        assert "Chose exact native resume" in rendered(app, "#checkpoints-decisions")


@pytest.mark.asyncio
async def test_handoffs_screen_shows_canonical_history(project: Path) -> None:
    from tests.tui.conftest import seed_handoff

    seed_handoff(project)

    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.show_section(TuiSection.HANDOFFS)
        await settle(app, pilot)

        assert app.query_one("#handoffs-table", DataTable).row_count == 1
        detail = rendered(app, "#handoffs-detail")
        assert "Handoff ID" in detail
        assert "codex" in detail
        assert "Historical observation" in detail


@pytest.mark.asyncio
async def test_providers_screen_shows_discovery_and_mcp_status(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.show_section(TuiSection.PROVIDERS)
        await settle(app, pilot)

        table = app.query_one("#providers-table", DataTable)
        assert table.row_count == 3

        detail = rendered(app, "#providers-detail")
        assert "Claude Code" in detail
        assert "2.1.0" in detail

        mcp = rendered(app, "#providers-mcp")
        assert "stdio" in mcp
        assert "get_project_context" in mcp
        assert "not configured" in mcp


@pytest.mark.asyncio
async def test_provider_screen_never_shows_credentials_or_account_details(project: Path) -> None:
    app = build_app(project)
    async with app.run_test() as pilot:
        await settle(app, pilot)
        app.show_section(TuiSection.PROVIDERS)
        await settle(app, pilot)

        body = rendered(app, "#providers-detail") + rendered(app, "#providers-mcp")
        for forbidden in ("token", "api_key", "ANTHROPIC_API_KEY", "credential", "keychain"):
            assert forbidden.lower() not in body.lower()


@pytest.mark.asyncio
async def test_empty_states_tell_the_operator_what_to_do_next(bare_project: Path) -> None:
    app = build_app(bare_project)
    async with app.run_test() as pilot:
        await settle(app, pilot)

        for section, selector, expected in (
            (TuiSection.SESSIONS, "#sessions-empty", "cortexshift run PROVIDER"),
            (TuiSection.CHECKPOINTS, "#checkpoints-empty", "No checkpoints yet."),
            (TuiSection.HANDOFFS, "#handoffs-empty", "No handoffs yet."),
        ):
            app.show_section(section)
            await settle(app, pilot)
            empty = app.query_one(selector, EmptyState)
            assert empty.display
            assert expected in rendered(app, selector)

        assert "No active task" in rendered(app, "#overview-task-title")


def _active_task_id(root: Path) -> str:
    from cortexshift.application.task_workspace import TaskWorkspaceService

    task = TaskWorkspaceService().get_active_task(root)
    assert task is not None
    return task.id
