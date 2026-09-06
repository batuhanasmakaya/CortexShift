"""The canonical mark-completed rule, and the project-root scoped Task service."""

from pathlib import Path

import pytest

from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.domain.errors import (
    NoActiveTaskError,
    ProjectNotInitializedError,
    TaskNotActivatableError,
    TaskNotFoundError,
)
from cortexshift.domain.task import Task, TaskStatus


def make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "project_id": "proj_1",
        "title": "Build the parser",
        "objective": "Parse the input safely",
        "completed_items": ["Scaffolded"],
        "remaining_items": ["Wire it up", "Add tests"],
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


# --- Task.complete_items: one canonical rule, shared by MCP and the TUI --------


def test_completing_an_item_moves_it_out_of_remaining() -> None:
    updated = make_task().complete_items(["Wire it up"])

    assert updated.completed == ["Scaffolded", "Wire it up"]
    assert updated.remaining == ["Add tests"]


def test_completing_an_item_never_completes_the_task() -> None:
    task = make_task(status=TaskStatus.IN_PROGRESS)
    updated = task.complete_items(["Wire it up", "Add tests"])

    assert updated.remaining == []
    assert updated.status is TaskStatus.IN_PROGRESS
    assert updated.is_terminal is False


def test_completing_deduplicates_and_trims() -> None:
    updated = make_task().complete_items(["  Scaffolded  ", "Wire it up", "Wire it up"])

    assert updated.completed == ["Scaffolded", "Wire it up"]
    assert updated.remaining == ["Add tests"]


def test_completing_an_item_that_is_not_remaining_still_records_it() -> None:
    updated = make_task().complete_items(["Something nobody planned"])

    assert "Something nobody planned" in updated.completed
    assert updated.remaining == ["Wire it up", "Add tests"]


def test_completing_nothing_changes_nothing() -> None:
    task = make_task()
    assert task.complete_items([]) is task
    assert task.complete_items(["   "]) is task


def test_only_exact_matches_are_removed_from_remaining() -> None:
    updated = make_task().complete_items(["wire it up"])

    assert "wire it up" in updated.completed
    # Case differs, so the planned item stands.
    assert "Wire it up" in updated.remaining


def test_the_mcp_facade_and_the_domain_agree_on_mark_completed() -> None:
    """The rule lives in one place; MCP and the TUI both call it."""
    import inspect

    from cortexshift.mcp.facade import McpApplicationFacade

    source = inspect.getsource(McpApplicationFacade.mark_completed)
    assert "complete_items" in source


# --- TaskWorkspaceService ------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ProjectInitializationService().initialize(tmp_path, name="Scoped")
    TaskWorkspaceService().start_task(
        title="First task",
        objective="Do the first thing",
        requirements=["A requirement"],
        constraints=["A constraint"],
        start_dir=tmp_path,
    )
    return tmp_path


def test_operations_resolve_the_project_by_path(workspace: Path) -> None:
    service = TaskWorkspaceService()
    task = service.get_active_task(workspace)

    assert task is not None
    assert task.title == "First task"
    assert task.requirements == ["A requirement"]
    assert [t.id for t in service.list_tasks(workspace)] == [task.id]


def test_operations_work_from_a_nested_subdirectory(workspace: Path) -> None:
    nested = workspace / "src" / "deep"
    nested.mkdir(parents=True)

    service = TaskWorkspaceService()
    service.add_remaining(["Found from a subdirectory"], start_dir=nested)

    task = service.get_active_task(workspace)
    assert task is not None
    assert "Found from a subdirectory" in task.remaining


def test_an_uninitialized_directory_is_reported_cleanly(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotInitializedError):
        TaskWorkspaceService().list_tasks(tmp_path / "nowhere")


def test_mutations_require_an_active_task(tmp_path: Path) -> None:
    ProjectInitializationService().initialize(tmp_path, name="Empty")
    service = TaskWorkspaceService()

    with pytest.raises(NoActiveTaskError):
        service.mark_completed(["Anything"], start_dir=tmp_path)
    with pytest.raises(NoActiveTaskError):
        service.set_current_work("Anything", start_dir=tmp_path)


def test_activation_follows_the_canonical_task_rules(workspace: Path) -> None:
    service = TaskWorkspaceService()
    second = service.start_task(
        title="Second task",
        objective="Do the second thing",
        set_active=False,
        start_dir=workspace,
    )

    activated = service.activate_task(second.id, start_dir=workspace)
    assert activated.id == second.id

    active = service.get_active_task(workspace)
    assert active is not None
    assert active.id == second.id

    with pytest.raises(TaskNotFoundError):
        service.activate_task("task_does_not_exist", start_dir=workspace)


def test_terminal_tasks_cannot_be_activated(workspace: Path) -> None:
    service = TaskWorkspaceService()
    with service.open(workspace) as opened:
        completed = opened.tasks.complete_task(opened.project.id)

    with pytest.raises(TaskNotActivatableError):
        service.activate_task(completed.id, start_dir=workspace)


def test_set_current_work_can_clear_the_entry(workspace: Path) -> None:
    service = TaskWorkspaceService()
    service.set_current_work("Working on it", start_dir=workspace)

    task = service.get_active_task(workspace)
    assert task is not None
    assert task.current_work == "Working on it"

    service.set_current_work(None, start_dir=workspace)
    cleared = service.get_active_task(workspace)
    assert cleared is not None
    assert cleared.current_work is None


def test_mark_completed_applies_the_canonical_rule_through_the_service(
    workspace: Path,
) -> None:
    service = TaskWorkspaceService()
    service.add_remaining(["Wire it up", "Add tests"], start_dir=workspace)

    updated = service.mark_completed(["Wire it up"], start_dir=workspace)

    assert "Wire it up" in updated.completed
    assert updated.remaining == ["Add tests"]
    assert updated.status is TaskStatus.IN_PROGRESS


def test_the_service_closes_the_store_it_opens(workspace: Path) -> None:
    """A dashboard refreshing every couple of seconds must not leak connections."""
    import warnings

    service = TaskWorkspaceService()
    with warnings.catch_warnings():
        warnings.simplefilter("error", ResourceWarning)
        for _ in range(25):
            service.list_tasks(workspace)
            service.get_active_task(workspace)
