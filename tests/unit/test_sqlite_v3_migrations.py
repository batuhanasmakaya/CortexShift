"""Unit tests for SQLite schema v2 to v3 migration regression."""

import sqlite3
from pathlib import Path

from cortexshift.adapters.sqlite.migrations import (
    CURRENT_SCHEMA_VERSION,
    _migrate_v1,
    _migrate_v2,
    get_current_schema_version,
)
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import TaskStatus


def test_migrate_v2_to_v3_preserves_state(tmp_path: Path) -> None:
    """Verify that genuine schema-v2 state (Project, Task, Snapshot) survives migration to v3."""
    db_file = tmp_path / "test_v2.sqlite3"
    conn = sqlite3.connect(str(db_file))

    # 1. Setup genuine v2 schema
    conn.execute(
        """
        CREATE TABLE schema_metadata (
            schema_version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        """
    )
    _migrate_v1(conn)
    _migrate_v2(conn)
    now_iso = utc_now().isoformat()
    conn.execute(
        "INSERT INTO schema_metadata (schema_version, applied_at) VALUES (1, ?), (2, ?);",
        (now_iso, now_iso),
    )

    # 2. Insert genuine Project, Task, runtime active task, and Git snapshot into v2 tables
    proj_id = generate_id("proj")
    task_id = generate_id("task")
    snap_id = generate_id("snap")

    conn.execute(
        """
        INSERT INTO projects (id, name, repo_path, created_at, metadata)
        VALUES (?, 'MigrationProject', '/path/to/mig', ?, '{}');
        """,
        (proj_id, now_iso),
    )
    conn.execute(
        """
        INSERT INTO tasks (
            id, project_id, title, objective, requirements, constraints,
            status, completed_items, current_work, remaining_items,
            known_issues, created_at, updated_at, metadata
        ) VALUES (
            ?, ?, 'V2 Task', 'Objective for V2', '["reqA"]', '["conA"]',
            'in_progress', '["item1"]', 'active work', '["item2"]',
            '["issue1"]', ?, ?, '{}'
        );
        """,
        (task_id, proj_id, now_iso, now_iso),
    )
    conn.execute(
        "INSERT INTO project_runtime (project_id, active_task_id) VALUES (?, ?);",
        (proj_id, task_id),
    )
    conn.execute(
        """
        INSERT INTO git_snapshots (
            id, project_id, project_root, git_root, git_version,
            branch, head_sha, detached_head, dirty,
            staged_files, modified_files, untracked_files, conflicted_files,
            working_tree_diff_summary, staged_diff_summary,
            captured_at, metadata
        ) VALUES (
            ?, ?, '/path/to/mig', '/path/to/mig', 'git version 2.40.0',
            'feature-branch', 'abc12345', 0, 1,
            '["staged.py"]', '["mod.py"]', '["untr.py"]', '[]',
            '1 file changed', '1 file staged',
            ?, '{}'
        );
        """,
        (snap_id, proj_id, now_iso),
    )
    conn.commit()
    assert get_current_schema_version(conn) == 2
    conn.close()

    # 3. Open with Phase 4 SQLiteStateStore (which runs auto_migrate to v3)
    with SQLiteStateStore(db_file, auto_migrate=True) as store:
        assert store.get_schema_version() == 3
        assert store.get_schema_version() == CURRENT_SCHEMA_VERSION

        # 4. Verify Project survived
        project = store.get_project(proj_id)
        assert project is not None
        assert project.name == "MigrationProject"
        assert project.repo_path == "/path/to/mig"

        # 5. Verify Task survived
        task = store.get_task(task_id)
        assert task is not None
        assert task.title == "V2 Task"
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.requirements == ["reqA"]

        # 6. Verify Active Task pointer survived
        active_task_id = store.get_active_task_id(proj_id)
        assert active_task_id == task_id

        # 7. Verify Git Snapshot survived
        snapshot = store.get_snapshot(snap_id)
        assert snapshot is not None
        assert snapshot.branch == "feature-branch"
        assert snapshot.head_sha == "abc12345"
        assert snapshot.staged_files == ["staged.py"]

        # 8. Verify Sessions table exists and is operational
        sessions = store.list_sessions(project_id=proj_id)
        assert sessions == []

        session = Session(
            task_id=task_id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.COMPLETED,
        )
        store.save_session(session)

        loaded_session = store.get_session(session.id)
        assert loaded_session is not None
        assert loaded_session.id == session.id
        assert loaded_session.task_id == task_id
        assert loaded_session.provider_id == PROVIDER_CLAUDE
        assert loaded_session.status == SessionStatus.COMPLETED
