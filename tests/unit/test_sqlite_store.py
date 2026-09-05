"""Unit tests for SQLiteStateStore adapter."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.errors import DatabaseStateError, StateCorruptionError
from cortexshift.domain.project import Project
from cortexshift.domain.task import Task, TaskStatus


def test_sqlite_store_pragmas_enabled(tmp_path: Path) -> None:
    """Verify that foreign keys, WAL mode, and busy timeout pragmas are active."""
    db_file = tmp_path / "test.sqlite3"
    with SQLiteStateStore(db_file) as store:
        cursor = store._conn.cursor()
        cursor.execute("PRAGMA foreign_keys;")
        assert cursor.fetchone()[0] == 1

        cursor.execute("PRAGMA journal_mode;")
        assert cursor.fetchone()[0].lower() == "wal"

        cursor.execute("PRAGMA busy_timeout;")
        assert cursor.fetchone()[0] == 5000


def test_project_roundtrip(tmp_path: Path) -> None:
    """Verify saving, retrieving, and getting default project."""
    db_file = tmp_path / "test.sqlite3"
    with SQLiteStateStore(db_file) as store:
        now = datetime(2026, 9, 5, 12, 0, 0, tzinfo=UTC)
        project = Project(
            name="AlphaProject",
            repo_path=str(tmp_path / "repo"),
            created_at=now,
            metadata={"framework": "fastapi"},
        )
        store.save_project(project)

        # Retrieve by ID
        loaded = store.get_project(project.id)
        assert loaded is not None
        assert loaded.id == project.id
        assert loaded.name == "AlphaProject"
        assert loaded.created_at == now
        assert loaded.metadata == {"framework": "fastapi"}

        # Retrieve default project
        default_proj = store.get_default_project()
        assert default_proj is not None
        assert default_proj.id == project.id

        # Update project
        updated = loaded.model_copy(update={"name": "AlphaProjectV2"})
        store.save_project(updated)
        loaded_updated = store.get_project(project.id)
        assert loaded_updated is not None
        assert loaded_updated.name == "AlphaProjectV2"


def test_task_roundtrip_and_list_fields(tmp_path: Path) -> None:
    """Verify task persistence, list collections, and UTC timestamp roundtrip."""
    db_file = tmp_path / "test.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name="Project", repo_path=str(tmp_path))
        store.save_project(project)

        created = datetime(2026, 9, 5, 10, 30, 0, tzinfo=UTC)
        updated = datetime(2026, 9, 5, 11, 45, 0, tzinfo=UTC)

        task = Task(
            project_id=project.id,
            title="Implement Core",
            objective="Build robust persistence layer",
            requirements=["req1", "req2"],
            constraints=["no-orm", "stdlib-sqlite"],
            status=TaskStatus.IN_PROGRESS,
            completed_items=["setup sqlite"],
            current_work="writing adapter",
            remaining_items=["tests", "docs"],
            known_issues=["test timeout"],
            created_at=created,
            updated_at=updated,
            metadata={"priority": "high"},
        )
        store.save_task(task)

        loaded = store.get_task(task.id)
        assert loaded is not None
        assert loaded.id == task.id
        assert loaded.project_id == project.id
        assert loaded.title == "Implement Core"
        assert loaded.objective == "Build robust persistence layer"
        assert loaded.requirements == ["req1", "req2"]
        assert loaded.constraints == ["no-orm", "stdlib-sqlite"]
        assert loaded.status == TaskStatus.IN_PROGRESS
        assert loaded.completed_items == ["setup sqlite"]
        assert loaded.current_work == "writing adapter"
        assert loaded.remaining_items == ["tests", "docs"]
        assert loaded.known_issues == ["test timeout"]
        assert loaded.created_at == created
        assert loaded.created_at.tzinfo == UTC
        assert loaded.updated_at == updated
        assert loaded.updated_at.tzinfo == UTC
        assert loaded.metadata == {"priority": "high"}

        # List tasks
        all_tasks = store.list_tasks(project.id)
        assert len(all_tasks) == 1
        assert all_tasks[0].id == task.id


def test_task_reopen_roundtrip_preserves_canonical_fields_and_order(tmp_path: Path) -> None:
    """Verify reopening store preserves all canonical fields, collections, and ordering."""
    db_file = tmp_path / "reopen_test.sqlite3"
    project = Project(name="ReopenProj", repo_path=str(tmp_path))

    expected_obj = "Ensure future coding agents never lose the original task objective."
    ordered_reqs = ["Alpha Requirement", "Beta Requirement", "Gamma Requirement"]
    ordered_consts = ["No external deps", "Standard library only", "Zero telemetry"]
    ordered_completed = ["Phase 0 done", "Phase 1 done"]
    ordered_remaining = ["Phase 2 hardening", "Phase 3 git"]
    ordered_issues = ["Issue 1: edge case", "Issue 2: performance"]

    # Initial session: save project and task
    with SQLiteStateStore(db_file) as store1:
        store1.save_project(project)
        task = Task(
            project_id=project.id,
            title="Preserve Canonical Task",
            objective=expected_obj,
            requirements=ordered_reqs,
            constraints=ordered_consts,
            status=TaskStatus.IN_PROGRESS,
            completed_items=ordered_completed,
            current_work="Testing SQLite serialization",
            remaining_items=ordered_remaining,
            known_issues=ordered_issues,
            metadata={"test_run": 42},
        )
        store1.save_task(task)
        store1.set_active_task_id(project.id, task.id)
        task_id = task.id

    # Reopen fresh store instance from disk
    with SQLiteStateStore(db_file, auto_migrate=False) as store2:
        reloaded = store2.get_task(task_id)
        assert reloaded is not None
        assert reloaded.title == "Preserve Canonical Task"
        assert reloaded.objective == expected_obj
        # Verify exact ordering preserved
        assert reloaded.requirements == ordered_reqs
        assert reloaded.constraints == ordered_consts
        assert reloaded.completed_items == ordered_completed
        assert reloaded.completed == ordered_completed
        assert reloaded.current_work == "Testing SQLite serialization"
        assert reloaded.remaining_items == ordered_remaining
        assert reloaded.remaining == ordered_remaining
        assert reloaded.known_issues == ordered_issues
        assert reloaded.metadata == {"test_run": 42}
        assert store2.get_active_task_id(project.id) == task_id


def test_active_task_pointer_roundtrip(tmp_path: Path) -> None:
    """Verify setting, retrieving, and clearing active task ID."""
    db_file = tmp_path / "test.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name="TestProj", repo_path=str(tmp_path))
        store.save_project(project)

        assert store.get_active_task_id(project.id) is None

        task = Task(project_id=project.id, title="Task 1", objective="Obj 1")
        store.save_task(task)

        store.set_active_task_id(project.id, task.id)
        assert store.get_active_task_id(project.id) == task.id

        store.set_active_task_id(project.id, None)
        assert store.get_active_task_id(project.id) is None


def test_foreign_key_cascade_deletion(tmp_path: Path) -> None:
    """Verify that deleting a project cascades to its tasks under active foreign keys."""
    db_file = tmp_path / "test.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name="TestProj", repo_path=str(tmp_path))
        store.save_project(project)

        task = Task(project_id=project.id, title="Task 1", objective="Obj 1")
        store.save_task(task)

        # Delete project
        with store._conn:
            store._conn.execute("DELETE FROM projects WHERE id = ?;", (project.id,))

        assert store.get_task(task.id) is None
        assert store.list_tasks(project.id) == []


def test_corrupted_json_raises_state_corruption_error(tmp_path: Path) -> None:
    """Verify that malformed JSON in task rows raises StateCorruptionError."""
    db_file = tmp_path / "test.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name="TestProj", repo_path=str(tmp_path))
        store.save_project(project)

        task = Task(project_id=project.id, title="Task 1", objective="Obj 1")
        store.save_task(task)

        # Corrupt the requirements column with invalid JSON
        with store._conn:
            store._conn.execute(
                "UPDATE tasks SET requirements = 'INVALID_JSON{' WHERE id = ?;",
                (task.id,),
            )

        with pytest.raises(StateCorruptionError):
            store.get_task(task.id)


def test_transaction_rollback_on_failed_save(tmp_path: Path) -> None:
    """Verify that a failing write does not commit partial state."""
    db_file = tmp_path / "test.sqlite3"
    with SQLiteStateStore(db_file) as store:
        # Attempt to save a task referencing a non-existent project_id (FK violation)
        orphan_task = Task(project_id="proj_nonexistent", title="Orphan", objective="None")
        with pytest.raises(DatabaseStateError):
            store.save_task(orphan_task)

        assert store.get_task(orphan_task.id) is None
