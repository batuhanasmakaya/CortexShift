"""Lightweight deterministic schema migration system for CortexShift SQLite database."""

import contextlib
import sqlite3
from collections.abc import Callable

from cortexshift.domain.errors import DatabaseStateError, UnsupportedSchemaVersionError
from cortexshift.domain.identifiers import utc_now

CURRENT_SCHEMA_VERSION = 5


def _migrate_v1(conn: sqlite3.Connection) -> None:
    """Apply Schema Version 1: initial Project, Task, and ProjectRuntime tables."""
    conn.execute(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            repo_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            metadata TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            requirements TEXT NOT NULL,
            constraints TEXT NOT NULL,
            status TEXT NOT NULL,
            completed_items TEXT NOT NULL,
            current_work TEXT,
            remaining_items TEXT NOT NULL,
            known_issues TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata TEXT NOT NULL
        );
        """
    )
    conn.execute("CREATE INDEX idx_tasks_project_id ON tasks(project_id);")
    conn.execute(
        """
        CREATE TABLE project_runtime (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            active_task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL
        );
        """
    )


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Apply Schema Version 2: Git snapshots table for repository context tracking."""
    conn.execute(
        """
        CREATE TABLE git_snapshots (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            project_root TEXT NOT NULL,
            git_root TEXT NOT NULL,
            git_version TEXT,
            branch TEXT,
            head_sha TEXT,
            detached_head INTEGER NOT NULL DEFAULT 0,
            dirty INTEGER NOT NULL DEFAULT 0,
            staged_files TEXT NOT NULL,
            modified_files TEXT NOT NULL,
            untracked_files TEXT NOT NULL,
            conflicted_files TEXT NOT NULL,
            working_tree_diff_summary TEXT,
            staged_diff_summary TEXT,
            captured_at TEXT NOT NULL,
            metadata TEXT NOT NULL
        );
        """
    )
    conn.execute("CREATE INDEX idx_git_snapshots_project_id ON git_snapshots(project_id);")
    conn.execute(
        "CREATE INDEX idx_git_snapshots_captured_at ON git_snapshots(project_id, captured_at DESC);"
    )


def _migrate_v3(conn: sqlite3.Connection) -> None:
    """Apply Schema Version 3: sessions table for agent execution history."""
    conn.execute(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            provider_id TEXT NOT NULL,
            native_session_id TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            exit_reason TEXT,
            exit_code INTEGER,
            metadata TEXT NOT NULL
        );
        """
    )
    conn.execute("CREATE INDEX idx_sessions_task_id ON sessions(task_id);")
    conn.execute("CREATE INDEX idx_sessions_started_at ON sessions(started_at DESC);")
    conn.execute("CREATE INDEX idx_sessions_provider_id ON sessions(provider_id);")


def _migrate_v4(conn: sqlite3.Connection) -> None:
    """Apply Schema Version 4: handoffs table for canonical cross-provider handoffs.

    ``payload`` stores the canonical ``HandoffPayload`` as validated JSON text. The
    payload is itself a strictly typed Pydantic schema, so canonical JSON keeps the
    column extensible without repr/pickle/eval or a wide, brittle column set.

    Deletion semantics: a handoff is meaningless without its project and task, so those
    cascade. Optional target-session and snapshot references null out, allowing handoff
    history to survive even if a referenced observation is removed.
    """
    conn.execute(
        """
        CREATE TABLE handoffs (
            id TEXT PRIMARY KEY,
            protocol_version INTEGER NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            source_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            source_provider_id TEXT NOT NULL,
            target_provider_id TEXT NOT NULL,
            git_snapshot_id TEXT REFERENCES git_snapshots(id) ON DELETE SET NULL,
            target_session_id TEXT REFERENCES sessions(id) ON DELETE SET NULL,
            status TEXT NOT NULL,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            delivered_at TEXT,
            failure_code TEXT,
            metadata TEXT NOT NULL
        );
        """
    )
    conn.execute("CREATE INDEX idx_handoffs_project_id ON handoffs(project_id);")
    conn.execute("CREATE INDEX idx_handoffs_task_id ON handoffs(task_id);")
    conn.execute("CREATE INDEX idx_handoffs_created_at ON handoffs(created_at DESC);")


def _migrate_v5(conn: sqlite3.Connection) -> None:
    """Add invocation lineage without rewriting any historical session."""
    conn.execute(
        "ALTER TABLE sessions ADD COLUMN resumed_from_session_id TEXT "
        "REFERENCES sessions(id) ON DELETE SET NULL;"
    )


# Ordered registry of migration functions: index 0 is v1, index 1 is v2, index 2 is v3, etc.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migrate_v1,
    _migrate_v2,
    _migrate_v3,
    _migrate_v4,
    _migrate_v5,
]


def get_current_schema_version(conn: sqlite3.Connection) -> int:
    """Inspect the database and return the highest applied schema version, or 0 if unversioned."""
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_metadata';"
        )
        if cursor.fetchone() is None:
            return 0

        cursor.execute("SELECT MAX(schema_version) FROM schema_metadata;")
        row = cursor.fetchone()
        if row is None or row[0] is None:
            return 0
        return int(row[0])
    except sqlite3.Error as err:
        raise DatabaseStateError(f"Failed to inspect database schema version: {err}") from err


def run_migrations(conn: sqlite3.Connection) -> int:
    """Run all pending schema migrations up to CURRENT_SCHEMA_VERSION in a transaction.

    Returns:
        The final schema version after migration.

    Raises:
        UnsupportedSchemaVersionError: If the database is at a higher version than supported.
        DatabaseStateError: If any migration step fails.
    """
    current_version = get_current_schema_version(conn)

    if current_version > CURRENT_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(current_version, CURRENT_SCHEMA_VERSION)

    if current_version == CURRENT_SCHEMA_VERSION:
        return current_version

    prev_isolation = conn.isolation_level
    try:
        conn.isolation_level = None
        conn.execute("BEGIN;")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_metadata (
                schema_version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )

        for target_ver in range(current_version + 1, CURRENT_SCHEMA_VERSION + 1):
            migration_fn = MIGRATIONS[target_ver - 1]
            migration_fn(conn)
            conn.execute(
                "INSERT INTO schema_metadata (schema_version, applied_at) VALUES (?, ?);",
                (target_ver, utc_now().isoformat()),
            )

        conn.execute("COMMIT;")
    except sqlite3.Error as err:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK;")
        msg = f"Migration to schema v{current_version + 1} failed: {err}"
        raise DatabaseStateError(msg) from err
    finally:
        conn.isolation_level = prev_isolation

    return CURRENT_SCHEMA_VERSION
