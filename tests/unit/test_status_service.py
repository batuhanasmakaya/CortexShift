"""Unit tests for ProjectStatusService."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.status_service import ProjectStatusService
from cortexshift.domain.errors import ProjectNotInitializedError
from cortexshift.domain.project import Project
from cortexshift.domain.task import Task


def test_status_uninitialized_directory(tmp_path: Path) -> None:
    """Verify get_status on uninitialized path raises ProjectNotInitializedError."""
    service = ProjectStatusService()
    with pytest.raises(ProjectNotInitializedError):
        service.get_status(tmp_path)


def test_status_without_active_task(tmp_path: Path) -> None:
    """Verify status report when no task is active."""
    state_dir = tmp_path / ".cortexshift"
    state_dir.mkdir()
    db_file = state_dir / "state.sqlite3"

    with SQLiteStateStore(db_file) as store:
        project = Project(name="IdleProj", repo_path=str(tmp_path))
        store.save_project(project)

    service = ProjectStatusService()
    status = service.get_status(tmp_path)

    assert status.name == "IdleProj"
    assert status.active_task is None
    assert status.schema_version == 2


def test_status_with_active_task_and_progress(tmp_path: Path) -> None:
    """Verify status report accurately summarizes active task and progress counts."""
    state_dir = tmp_path / ".cortexshift"
    state_dir.mkdir()
    db_file = state_dir / "state.sqlite3"

    with SQLiteStateStore(db_file) as store:
        project = Project(name="ActiveProj", repo_path=str(tmp_path))
        store.save_project(project)

        task = Task(
            project_id=project.id,
            title="Core Feature",
            objective="Deliver MVP",
            completed_items=["item 1", "item 2", "item 3"],
            remaining_items=["item 4", "item 5"],
            known_issues=["issue 1"],
        )
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)

    service = ProjectStatusService()

    # Query from a nested subdirectory to verify discovery
    sub_dir = tmp_path / "src" / "pkg"
    sub_dir.mkdir(parents=True)

    status = service.get_status(sub_dir)
    assert status.name == "ActiveProj"
    assert status.active_task is not None
    assert status.active_task.id == task.id
    assert status.active_task.title == "Core Feature"
    assert status.active_task.progress.completed == 3
    assert status.active_task.progress.remaining == 2
    assert status.active_task.progress.issues == 1
