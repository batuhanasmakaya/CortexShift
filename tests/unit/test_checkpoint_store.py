"""Unit tests for CheckpointStore persistence on SQLiteStateStore."""

import sqlite3
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.checkpoint import (
    CheckpointGitState,
    CheckpointKind,
    CheckpointPayload,
    CheckpointRecord,
    CheckpointTaskSnapshot,
    CheckpointTestProvenance,
    CheckpointTestStatus,
)
from cortexshift.domain.errors import DatabaseStateError
from cortexshift.domain.git import GitSnapshot, RepositoryInspectionStatus
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task


def _make_checkpoint(
    project_id: str,
    task_id: str,
    session_id: str | None = None,
    snapshot_id: str | None = None,
    kind: CheckpointKind = CheckpointKind.MANUAL,
    operator_note: str | None = None,
) -> CheckpointRecord:
    payload = CheckpointPayload(
        task=CheckpointTaskSnapshot(
            task_id=task_id,
            task_title="Checkpoint Task",
            task_status="in_progress",
            objective="Test checkpoint persistence",
            completed=["item1"],
            current_work="item2",
            remaining=["item3"],
        ),
        git_state=CheckpointGitState(
            status=RepositoryInspectionStatus.READY,
            available=True,
            note="Git inspection ready",
            branch="main",
            dirty=False,
            snapshot_id=snapshot_id,
        ),
        files_touched=["foo.py"],
        decisions=["Decision 1"],
        test_status=CheckpointTestStatus(
            known=True,
            summary="10 passed",
            provenance=CheckpointTestProvenance.REPORTED,
        ),
        operator_note=operator_note,
    )
    return CheckpointRecord(
        project_id=project_id,
        task_id=task_id,
        session_id=session_id,
        git_snapshot_id=snapshot_id,
        kind=kind,
        payload=payload,
    )


def test_checkpoint_store_crud(tmp_path: Path) -> None:
    db_file = tmp_path / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name="Proj", repo_path=str(tmp_path))
        store.save_project(project)

        task = Task(project_id=project.id, title="Task", objective="Objective")
        store.save_task(task)

        session = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
        store.save_session(session)

        snapshot = GitSnapshot(
            project_id=project.id,
            project_root=str(tmp_path),
            git_root=str(tmp_path),
            dirty=False,
        )
        store.save_snapshot(snapshot)

        cp1 = _make_checkpoint(
            project_id=project.id,
            task_id=task.id,
            session_id=session.id,
            snapshot_id=snapshot.id,
            kind=CheckpointKind.MANUAL,
            operator_note="First checkpoint",
        )
        store.save_checkpoint(cp1)

        # Get by ID
        loaded = store.get_checkpoint(cp1.id)
        assert loaded is not None
        assert loaded.id == cp1.id
        assert loaded.project_id == project.id
        assert loaded.task_id == task.id
        assert loaded.session_id == session.id
        assert loaded.git_snapshot_id == snapshot.id
        assert loaded.kind == CheckpointKind.MANUAL
        assert loaded.payload.operator_note == "First checkpoint"
        assert loaded.payload.decisions == ["Decision 1"]

        # Non-existent returns None
        assert store.get_checkpoint("cp_nonexistent") is None

        # Add second checkpoint
        cp2 = _make_checkpoint(
            project_id=project.id,
            task_id=task.id,
            session_id=session.id,
            kind=CheckpointKind.SESSION_END,
            operator_note="Second checkpoint",
        )
        store.save_checkpoint(cp2)

        # List checkpoints
        all_cps = store.list_checkpoints(task_id=task.id)
        assert len(all_cps) == 2
        # Reverse chronological: newest first
        assert all_cps[0].id == cp2.id
        assert all_cps[1].id == cp1.id

        # Latest checkpoint
        latest = store.get_latest_checkpoint(task.id)
        assert latest is not None
        assert latest.id == cp2.id


def test_checkpoint_store_foreign_keys_and_cascades(tmp_path: Path) -> None:
    db_file = tmp_path / "state_fks.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name="Proj", repo_path=str(tmp_path))
        store.save_project(project)

        task = Task(project_id=project.id, title="Task", objective="Objective")
        store.save_task(task)

        session = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
        store.save_session(session)

        snapshot = GitSnapshot(
            project_id=project.id,
            project_root=str(tmp_path),
            git_root=str(tmp_path),
            dirty=False,
        )
        store.save_snapshot(snapshot)

        # Invalid project_id raises DatabaseStateError
        cp_bad_proj = _make_checkpoint(project_id="proj_missing", task_id=task.id)
        with pytest.raises(DatabaseStateError):
            store.save_checkpoint(cp_bad_proj)

        # Invalid task_id raises DatabaseStateError
        cp_bad_task = _make_checkpoint(project_id=project.id, task_id="task_missing")
        with pytest.raises(DatabaseStateError):
            store.save_checkpoint(cp_bad_task)

        cp = _make_checkpoint(
            project_id=project.id,
            task_id=task.id,
            session_id=session.id,
            snapshot_id=snapshot.id,
        )
        store.save_checkpoint(cp)

    # Delete session -> session_id becomes NULL
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("DELETE FROM sessions WHERE id = ?;", (session.id,))
    conn.commit()
    conn.close()

    with SQLiteStateStore(db_file) as store:
        loaded = store.get_checkpoint(cp.id)
        assert loaded is not None
        assert loaded.session_id is None
        assert loaded.git_snapshot_id == snapshot.id

    # Delete snapshot -> git_snapshot_id becomes NULL
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("DELETE FROM git_snapshots WHERE id = ?;", (snapshot.id,))
    conn.commit()
    conn.close()

    with SQLiteStateStore(db_file) as store:
        loaded = store.get_checkpoint(cp.id)
        assert loaded is not None
        assert loaded.git_snapshot_id is None

    # Delete task -> checkpoint is CASCADE deleted
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("DELETE FROM tasks WHERE id = ?;", (task.id,))
    conn.commit()
    conn.close()

    with SQLiteStateStore(db_file) as store:
        assert store.get_checkpoint(cp.id) is None


def test_checkpoint_store_survives_process_reopen(tmp_path: Path) -> None:
    db_file = tmp_path / "state_reopen.sqlite3"
    with SQLiteStateStore(db_file) as store:
        project = Project(name="Proj", repo_path=str(tmp_path))
        store.save_project(project)
        task = Task(project_id=project.id, title="Task", objective="Objective")
        store.save_task(task)

        cp = _make_checkpoint(project_id=project.id, task_id=task.id, operator_note="Saved!")
        store.save_checkpoint(cp)

    # Fresh store connection
    with SQLiteStateStore(db_file) as store:
        reopened = store.get_checkpoint(cp.id)
        assert reopened is not None
        assert reopened.id == cp.id
        assert reopened.payload.operator_note == "Saved!"
