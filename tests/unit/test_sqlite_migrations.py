"""Unit tests for SQLite schema migration runner."""

import sqlite3
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.migrations import (
    CURRENT_SCHEMA_VERSION,
    _migrate_v1,
    get_current_schema_version,
    run_migrations,
)
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.errors import DatabaseStateError, UnsupportedSchemaVersionError
from cortexshift.domain.git import GitSnapshot
from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.domain.task import TaskStatus


def test_fresh_database_migrates_to_latest_version(tmp_path: Path) -> None:
    """Verify an empty database successfully runs migrations up to CURRENT_SCHEMA_VERSION."""
    db_file = tmp_path / "test.sqlite3"
    conn = sqlite3.connect(str(db_file))

    try:
        assert get_current_schema_version(conn) == 0
        final_version = run_migrations(conn)
        assert final_version == CURRENT_SCHEMA_VERSION
        assert get_current_schema_version(conn) == CURRENT_SCHEMA_VERSION

        # Verify expected tables exist
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;")
        tables = [row[0] for row in cursor.fetchall()]
        assert "schema_metadata" in tables
        assert "projects" in tables
        assert "tasks" in tables
        assert "project_runtime" in tables
        assert "git_snapshots" in tables
    finally:
        conn.close()


def test_migrate_v1_to_v2_preserves_state(tmp_path: Path) -> None:
    """Verify that a genuine v1 database migrates forward to v2 preserving all state."""
    db_file = tmp_path / "test_v1.sqlite3"
    conn = sqlite3.connect(str(db_file))

    # 1. Setup genuine v1 schema
    conn.execute(
        """
        CREATE TABLE schema_metadata (
            schema_version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        """
    )
    _migrate_v1(conn)
    conn.execute(
        "INSERT INTO schema_metadata (schema_version, applied_at) VALUES (1, ?);",
        (utc_now().isoformat(),),
    )

    # 2. Insert Phase 2 baseline state into v1 tables
    proj_id = generate_id("proj")
    task_id = generate_id("task")
    now_iso = utc_now().isoformat()

    conn.execute(
        """
        INSERT INTO projects (id, name, repo_path, created_at, metadata)
        VALUES (?, 'v1-project', '/path/to/v1', ?, '{}');
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
            ?, ?, 'v1 Task', 'Test v1 preservation', '["req1"]', '["con1"]',
            'in_progress', '["done1"]', 'migrating', '["rem1"]',
            '[]', ?, ?, '{}'
        );
        """,
        (task_id, proj_id, now_iso, now_iso),
    )
    conn.execute(
        "INSERT INTO project_runtime (project_id, active_task_id) VALUES (?, ?);",
        (proj_id, task_id),
    )
    conn.commit()
    conn.close()

    # 3. Open through Phase 3 SQLiteStateStore (which runs auto_migrate)
    with SQLiteStateStore(db_file, auto_migrate=True) as store:
        assert store.get_schema_version() == 2

        # 4. Verify project, task, and active task survived
        project = store.get_project(proj_id)
        assert project is not None
        assert project.name == "v1-project"
        assert project.repo_path == "/path/to/v1"

        task = store.get_task(task_id)
        assert task is not None
        assert task.title == "v1 Task"
        assert task.objective == "Test v1 preservation"
        assert task.requirements == ["req1"]
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.completed_items == ["done1"]

        active_id = store.get_active_task_id(proj_id)
        assert active_id == task_id

        # 5. Verify git_snapshots table is available and functional
        snapshots = store.list_snapshots(proj_id)
        assert snapshots == []

        # 6. Save a GitSnapshot to the migrated database
        snap = GitSnapshot(
            project_id=proj_id,
            project_root="/path/to/v1",
            git_root="/path/to/v1",
            branch="main",
            head_sha="deadbeef1234",
            dirty=True,
            staged_files=["file1.py"],
        )
        store.save_snapshot(snap)

        # 7. Retrieve and verify
        loaded = store.get_snapshot(snap.id)
        assert loaded is not None
        assert loaded.id == snap.id
        assert loaded.branch == "main"
        assert loaded.head_sha == "deadbeef1234"
        assert loaded.staged_files == ["file1.py"]


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Verify running migrations on an already up-to-date database is a no-op."""
    db_file = tmp_path / "test.sqlite3"
    conn = sqlite3.connect(str(db_file))

    try:
        v1 = run_migrations(conn)
        assert v1 == CURRENT_SCHEMA_VERSION

        # Second run
        v2 = run_migrations(conn)
        assert v2 == CURRENT_SCHEMA_VERSION
    finally:
        conn.close()


def test_unsupported_future_schema_version_rejected(tmp_path: Path) -> None:
    """Verify that opening a database with a higher schema version fails safely."""
    db_file = tmp_path / "test.sqlite3"
    conn = sqlite3.connect(str(db_file))

    try:
        conn.execute(
            """
            CREATE TABLE schema_metadata (
                schema_version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO schema_metadata (schema_version, applied_at) VALUES (?, ?);",
            (999, "2026-09-05T00:00:00Z"),
        )
        conn.commit()

        assert get_current_schema_version(conn) == 999

        with pytest.raises(UnsupportedSchemaVersionError) as exc_info:
            run_migrations(conn)

        assert exc_info.value.current_version == 999
        assert exc_info.value.max_supported_version == CURRENT_SCHEMA_VERSION
        assert "newer incompatible version" in str(exc_info.value)
    finally:
        conn.close()


def test_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that if a migration step fails, transaction rolls back cleanly."""
    db_file = tmp_path / "test.sqlite3"
    conn = sqlite3.connect(str(db_file))

    def broken_migration(conn: sqlite3.Connection) -> None:
        conn.execute("CREATE TABLE valid_table (id TEXT);")
        conn.execute("INVALID SQL SYNTAX HERE;")

    import cortexshift.adapters.sqlite.migrations as mig_mod

    monkeypatch.setattr(mig_mod, "MIGRATIONS", [broken_migration])

    try:
        with pytest.raises(DatabaseStateError):
            run_migrations(conn)

        # Confirm rollback: valid_table should not exist
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='valid_table';")
        assert cursor.fetchone() is None
    finally:
        conn.close()
