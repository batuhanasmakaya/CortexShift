"""Unit tests for SessionStore persistence and lifecycle operations."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.errors import DatabaseStateError
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task


def _setup_project_and_task(store: SQLiteStateStore) -> tuple[Project, Task]:
    """Helper to initialize project and task for session tests."""
    project = Project(name="SessionTestProject", repo_path="/path/to/repo")
    store.save_project(project)

    task = Task(
        project_id=project.id,
        title="Test Task",
        objective="Verify session store",
    )
    store.save_task(task)
    store.set_active_task_id(project.id, task.id)
    return project, task


def test_session_crud_and_lifecycle(tmp_path: Path) -> None:
    """Verify creating, updating, and querying sessions in SQLite."""
    db_file = tmp_path / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project, task = _setup_project_and_task(store)

        session = Session(
            task_id=task.id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.RUNNING,
            started_at=utc_now(),
        )
        store.save_session(session)

        loaded = store.get_session(session.id)
        assert loaded is not None
        assert loaded.id == session.id
        assert loaded.task_id == task.id
        assert loaded.provider_id == PROVIDER_CLAUDE
        assert loaded.status == SessionStatus.RUNNING
        assert loaded.ended_at is None
        assert loaded.exit_code is None
        assert loaded.exit_reason is None

        # Update session to COMPLETED
        now = utc_now()
        completed = loaded.model_copy(
            update={
                "status": SessionStatus.COMPLETED,
                "ended_at": now,
                "exit_code": 0,
                "exit_reason": SessionExitReason.NORMAL_COMPLETION,
            }
        )
        store.save_session(completed)

        updated = store.get_session(session.id)
        assert updated is not None
        assert updated.status == SessionStatus.COMPLETED
        assert updated.ended_at is not None
        assert updated.exit_code == 0
        assert updated.exit_reason == SessionExitReason.NORMAL_COMPLETION


def test_session_listing_and_filtering(tmp_path: Path) -> None:
    """Verify list_sessions filters by project and task and respects limits."""
    db_file = tmp_path / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project, task1 = _setup_project_and_task(store)

        task2 = Task(
            project_id=project.id,
            title="Second Task",
            objective="Second objective",
        )
        store.save_task(task2)

        # Create sessions
        s1 = Session(task_id=task1.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.COMPLETED)
        s2 = Session(task_id=task1.id, provider_id=PROVIDER_CODEX, status=SessionStatus.FAILED)
        s3 = Session(task_id=task2.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.COMPLETED)

        store.save_session(s1)
        store.save_session(s2)
        store.save_session(s3)

        # All sessions for project
        proj_sessions = store.list_sessions(project_id=project.id)
        assert len(proj_sessions) == 3

        # Filter by task1
        task1_sessions = store.list_sessions(task_id=task1.id)
        assert len(task1_sessions) == 2
        assert {s.id for s in task1_sessions} == {s1.id, s2.id}

        # Filter by task2
        task2_sessions = store.list_sessions(task_id=task2.id)
        assert len(task2_sessions) == 1
        assert task2_sessions[0].id == s3.id

        # Limit
        limited = store.list_sessions(project_id=project.id, limit=2)
        assert len(limited) == 2


def test_session_foreign_key_constraint(tmp_path: Path) -> None:
    """Verify persisting session with non-existent task_id fails foreign key constraint."""
    db_file = tmp_path / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        session = Session(
            task_id="nonexistent_task_id",
            provider_id=PROVIDER_CLAUDE,
        )
        with pytest.raises(DatabaseStateError):
            store.save_session(session)


def test_session_reopen_durability(tmp_path: Path) -> None:
    """Verify sessions survive closing and reopening SQLite connection."""
    db_file = tmp_path / "state.sqlite3"

    with SQLiteStateStore(db_file) as store1:
        project, task = _setup_project_and_task(store1)
        session = Session(
            task_id=task.id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.COMPLETED,
            exit_code=0,
            exit_reason=SessionExitReason.NORMAL_COMPLETION,
            metadata={"custom_flag": True},
        )
        store1.save_session(session)
        sess_id = session.id
        proj_id = project.id

    # Reopen fresh store instance
    with SQLiteStateStore(db_file) as store2:
        loaded = store2.get_session(sess_id)
        assert loaded is not None
        assert loaded.id == sess_id
        assert loaded.status == SessionStatus.COMPLETED
        assert loaded.exit_code == 0
        assert loaded.metadata == {"custom_flag": True}

        all_sess = store2.list_sessions(project_id=proj_id)
        assert len(all_sess) == 1
        assert all_sess[0].id == sess_id
