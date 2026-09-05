"""Unit tests for ProjectInitializationService."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.domain.errors import ProjectConflictError
from cortexshift.domain.task import Task


def test_fresh_initialization(tmp_path: Path) -> None:
    """Verify fresh project initialization creates state directory, database, and project row."""
    service = ProjectInitializationService()
    result = service.initialize(target_path=tmp_path, name="TestProject")

    assert result.already_initialized is False
    assert result.project.name == "TestProject"
    assert result.project.repo_path == str(tmp_path.resolve())
    assert (tmp_path / ".cortexshift" / "state.sqlite3").is_file()

    # Verify database contents
    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3") as store:
        proj = store.get_default_project()
        assert proj is not None
        assert proj.id == result.project.id
        assert proj.name == "TestProject"
        assert store.get_schema_version() == 3


def test_initialization_defaults_name_to_directory_name(tmp_path: Path) -> None:
    """Verify omitting --name derives project name from directory name."""
    project_dir = tmp_path / "MySpecialProject"
    project_dir.mkdir()

    service = ProjectInitializationService()
    result = service.initialize(target_path=project_dir)

    assert result.project.name == "MySpecialProject"


def test_initialization_is_idempotent_and_preserves_state(tmp_path: Path) -> None:
    """Verify re-running init does not duplicate project or destroy tasks."""
    service = ProjectInitializationService()
    first_result = service.initialize(target_path=tmp_path, name="DurableProject")
    project_id = first_result.project.id

    # Add a task to this project
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store:
        task = Task(project_id=project_id, title="Important Task", objective="Must survive init")
        store.save_task(task)

    # Re-initialize
    second_result = service.initialize(target_path=tmp_path)
    assert second_result.already_initialized is True
    assert second_result.project.id == project_id
    assert second_result.project.name == "DurableProject"

    # Verify task still exists
    with SQLiteStateStore(db_path) as store:
        loaded_task = store.get_task(task.id)
        assert loaded_task is not None
        assert loaded_task.title == "Important Task"


def test_initialization_detects_path_conflict(tmp_path: Path) -> None:
    """Verify that if existing database records a different path, it raises conflict error."""
    service = ProjectInitializationService()
    service.initialize(target_path=tmp_path, name="Original")

    # Manually modify the repo_path in the database to simulate copied state
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as store, store._conn:
        store._conn.execute("UPDATE projects SET repo_path = '/completely/different/location';")

    with pytest.raises(ProjectConflictError):
        service.initialize(target_path=tmp_path)
