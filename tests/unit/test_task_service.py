"""Unit tests for TaskService lifecycle and active task state management."""

from collections.abc import Generator
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.task_service import TaskService
from cortexshift.domain.errors import (
    NoActiveTaskError,
    TaskAlreadyCompletedError,
    TaskNotActivatableError,
    TaskNotFoundError,
)
from cortexshift.domain.project import Project
from cortexshift.domain.task import TaskStatus


@pytest.fixture
def store_and_project(
    tmp_path: Path,
) -> Generator[tuple[SQLiteStateStore, Project], None, None]:
    db_file = tmp_path / "test.sqlite3"
    store = SQLiteStateStore(db_file)
    project = Project(name="TestProj", repo_path=str(tmp_path))
    store.save_project(project)
    yield store, project
    store.close()


def test_start_task_activates_it(store_and_project: tuple[SQLiteStateStore, Project]) -> None:
    """Verify start_task saves task and sets active pointer."""
    store, project = store_and_project
    service = TaskService(store)

    task = service.start_task(
        project_id=project.id,
        title="Screen understanding",
        objective="Add vision capability",
        requirements=["support png", "support jpeg"],
        constraints=["no cloud api"],
    )

    assert task.status == TaskStatus.IN_PROGRESS
    assert task.requirements == ["support png", "support jpeg"]
    assert task.constraints == ["no cloud api"]

    active = service.get_active_task(project.id)
    assert active is not None
    assert active.id == task.id


def test_start_task_no_set_active(store_and_project: tuple[SQLiteStateStore, Project]) -> None:
    """Verify start_task with set_active=False creates PENDING task without setting active."""
    store, project = store_and_project
    service = TaskService(store)

    task = service.start_task(
        project_id=project.id,
        title="Background Task",
        objective="Prepare future work",
        set_active=False,
    )

    assert task.status == TaskStatus.PENDING
    assert service.get_active_task(project.id) is None

    loaded = service.get_task(task.id)
    assert loaded.status == TaskStatus.PENDING


def test_start_second_task_changes_active_pointer(
    store_and_project: tuple[SQLiteStateStore, Project],
) -> None:
    """Verify starting task 2 makes it active without modifying task 1."""
    store, project = store_and_project
    service = TaskService(store)

    task1 = service.start_task(project.id, "Task 1", "Obj 1")
    task2 = service.start_task(project.id, "Task 2", "Obj 2")

    active = service.get_active_task(project.id)
    assert active is not None
    assert active.id == task2.id

    # Task 1 still exists and remains in_progress
    loaded_task1 = service.get_task(task1.id)
    assert loaded_task1.status == TaskStatus.IN_PROGRESS


def test_activate_previous_task(store_and_project: tuple[SQLiteStateStore, Project]) -> None:
    """Verify switching active pointer to an existing task."""
    store, project = store_and_project
    service = TaskService(store)

    task1 = service.start_task(project.id, "Task 1", "Obj 1")
    service.start_task(project.id, "Task 2", "Obj 2")

    service.activate_task(project.id, task1.id)
    active = service.get_active_task(project.id)
    assert active is not None
    assert active.id == task1.id


def test_activate_terminal_task_rejected(
    store_and_project: tuple[SQLiteStateStore, Project],
) -> None:
    """Verify activating a completed task raises TaskNotActivatableError."""
    store, project = store_and_project
    service = TaskService(store)

    task = service.start_task(project.id, "Task 1", "Obj 1")
    service.complete_task(project.id, task.id)

    with pytest.raises(TaskNotActivatableError):
        service.activate_task(project.id, task.id)


def test_complete_active_task_clears_pointer(
    store_and_project: tuple[SQLiteStateStore, Project],
) -> None:
    """Verify completing active task marks it completed and clears active pointer."""
    store, project = store_and_project
    service = TaskService(store)

    task = service.start_task(project.id, "Task 1", "Obj 1")
    completed = service.complete_task(project.id)

    assert completed.id == task.id
    assert completed.status == TaskStatus.COMPLETED
    assert service.get_active_task(project.id) is None


def test_complete_specific_non_active_task(
    store_and_project: tuple[SQLiteStateStore, Project],
) -> None:
    """Verify completing an inactive task leaves active pointer untouched."""
    store, project = store_and_project
    service = TaskService(store)

    task1 = service.start_task(project.id, "Task 1", "Obj 1")
    task2 = service.start_task(project.id, "Task 2", "Obj 2")

    # Complete task 1 explicitly
    service.complete_task(project.id, task_id=task1.id)

    # Task 2 should still be active
    active = service.get_active_task(project.id)
    assert active is not None
    assert active.id == task2.id

    loaded_task1 = service.get_task(task1.id)
    assert loaded_task1.status == TaskStatus.COMPLETED


def test_complete_already_completed_task_fails(
    store_and_project: tuple[SQLiteStateStore, Project],
) -> None:
    """Verify attempting to complete a completed task raises TaskAlreadyCompletedError."""
    store, project = store_and_project
    service = TaskService(store)

    task = service.start_task(project.id, "Task 1", "Obj 1")
    service.complete_task(project.id, task.id)

    with pytest.raises(TaskAlreadyCompletedError):
        service.complete_task(project.id, task.id)


def test_update_task_progress_and_deduplication(
    store_and_project: tuple[SQLiteStateStore, Project],
) -> None:
    """Verify updating task lists appends items without duplicates."""
    store, project = store_and_project
    service = TaskService(store)

    service.start_task(project.id, "Task 1", "Obj 1")

    # Initial update
    service.update_task(
        project_id=project.id,
        current_work="Testing updates",
        add_completed=["Step 1", "Step 2"],
        add_remaining=["Step 3"],
        add_issues=["Issue A"],
    )

    # Second update with a duplicate item
    updated = service.update_task(
        project_id=project.id,
        add_completed=["Step 2", "Step 2.5"],
        add_remaining=["Step 3", "Step 4"],
        add_issues=["Issue A", "Issue B"],
    )

    assert updated.completed_items == ["Step 1", "Step 2", "Step 2.5"]
    assert updated.remaining_items == ["Step 3", "Step 4"]
    assert updated.known_issues == ["Issue A", "Issue B"]
    assert updated.current_work == "Testing updates"

    # Clear current work
    cleared = service.update_task(project_id=project.id, clear_current_work=True)
    assert cleared.current_work is None


def test_operations_on_nonexistent_or_unowned_task(
    store_and_project: tuple[SQLiteStateStore, Project],
) -> None:
    """Verify proper errors for missing tasks and missing active tasks."""
    store, project = store_and_project
    service = TaskService(store)

    with pytest.raises(TaskNotFoundError):
        service.get_task("task_nonexistent")

    with pytest.raises(NoActiveTaskError):
        service.complete_task(project.id)

    with pytest.raises(NoActiveTaskError):
        service.update_task(project.id, current_work="anything")
