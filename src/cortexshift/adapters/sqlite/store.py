"""SQLite implementation of the StateStore persistence port."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cortexshift.adapters.sqlite.migrations import get_current_schema_version, run_migrations
from cortexshift.domain.errors import (
    DatabaseStateError,
    StateCorruptionError,
    UnsupportedSchemaVersionError,
)
from cortexshift.domain.git import GitSnapshot
from cortexshift.domain.project import Project
from cortexshift.domain.task import Task, TaskStatus
from cortexshift.ports.repository import RepositorySnapshotStore
from cortexshift.ports.state_store import StateStore


def _parse_utc_datetime(iso_str: str) -> datetime:
    """Parse an ISO 8601 string and ensure it has timezone-aware UTC tzinfo."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class SQLiteStateStore(StateStore, RepositorySnapshotStore):
    """SQLite-backed StateStore managing project-local canonical state."""

    def __init__(self, db_path: Path | str, auto_migrate: bool = True) -> None:
        """Initialize the SQLite state store at the given database path.

        Args:
            db_path: Absolute or relative path to the sqlite3 database file.
            auto_migrate: Whether to run pending schema migrations automatically on initialization.
        """
        self.db_path = Path(db_path).resolve()
        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            self._conn = sqlite3.connect(
                database=str(self.db_path),
                timeout=5.0,
            )
            self._conn.row_factory = sqlite3.Row
            self._configure_connection()
        except sqlite3.Error as err:
            msg = f"Failed to open SQLite database at {self.db_path}: {err}"
            raise DatabaseStateError(msg) from err

        if auto_migrate:
            self.migrate()

    def _configure_connection(self) -> None:
        """Configure SQLite pragmas for safety and performance."""
        try:
            # Setting journal_mode to WAL requires autocommit mode (no active transaction)
            self._conn.isolation_level = None
            self._conn.execute("PRAGMA journal_mode = WAL;")
            self._conn.execute("PRAGMA foreign_keys = ON;")
            self._conn.execute("PRAGMA busy_timeout = 5000;")
            self._conn.isolation_level = "DEFERRED"
        except sqlite3.Error as err:
            msg = f"Failed to configure SQLite database pragmas: {err}"
            raise DatabaseStateError(msg) from err

    def migrate(self) -> int:
        """Run pending schema migrations on the database."""
        try:
            return run_migrations(self._conn)
        except (DatabaseStateError, UnsupportedSchemaVersionError):
            raise
        except sqlite3.Error as err:
            raise DatabaseStateError(f"Migration failed: {err}") from err

    def get_schema_version(self) -> int:
        """Inspect and return the current schema version of the database."""
        return get_current_schema_version(self._conn)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if hasattr(self, "_conn"):
            self._conn.close()

    def __enter__(self) -> "SQLiteStateStore":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    # --- Project Operations ---

    def save_project(self, project: Project) -> None:
        """Persist or update a canonical Project record."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO projects (id, name, repo_path, created_at, metadata)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        name = excluded.name,
                        repo_path = excluded.repo_path,
                        metadata = excluded.metadata;
                    """,
                    (
                        project.id,
                        project.name,
                        str(Path(project.repo_path).resolve()),
                        project.created_at.isoformat(),
                        json.dumps(project.metadata, ensure_ascii=False),
                    ),
                )
                # Ensure a runtime row exists for this project
                self._conn.execute(
                    """
                    INSERT INTO project_runtime (project_id, active_task_id)
                    VALUES (?, NULL)
                    ON CONFLICT(project_id) DO NOTHING;
                    """,
                    (project.id,),
                )
        except sqlite3.Error as err:
            raise DatabaseStateError(f"Failed to save project '{project.id}': {err}") from err

    def get_project(self, project_id: str) -> Project | None:
        """Retrieve a Project by its stable identifier."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT id, name, repo_path, created_at, metadata FROM projects WHERE id = ?;",
                (project_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_project(row)
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            raise StateCorruptionError(f"Failed to load project '{project_id}': {err}") from err

    def get_default_project(self) -> Project | None:
        """Retrieve the canonical project associated with this project-local store."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT id, name, repo_path, created_at, metadata FROM projects LIMIT 1;"
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_project(row)
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            raise StateCorruptionError(f"Failed to load default project: {err}") from err

    def _row_to_project(self, row: sqlite3.Row) -> Project:
        """Convert a database row into a Project domain entity."""
        return Project(
            id=row["id"],
            name=row["name"],
            repo_path=row["repo_path"],
            created_at=_parse_utc_datetime(row["created_at"]),
            metadata=json.loads(row["metadata"]),
        )

    # --- Task Operations ---

    def save_task(self, task: Task) -> None:
        """Persist or update a canonical Task record."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO tasks (
                        id, project_id, title, objective, requirements, constraints,
                        status, completed_items, current_work, remaining_items,
                        known_issues, created_at, updated_at, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        title = excluded.title,
                        objective = excluded.objective,
                        requirements = excluded.requirements,
                        constraints = excluded.constraints,
                        status = excluded.status,
                        completed_items = excluded.completed_items,
                        current_work = excluded.current_work,
                        remaining_items = excluded.remaining_items,
                        known_issues = excluded.known_issues,
                        updated_at = excluded.updated_at,
                        metadata = excluded.metadata;
                    """,
                    (
                        task.id,
                        task.project_id,
                        task.title,
                        task.objective,
                        json.dumps(task.requirements, ensure_ascii=False),
                        json.dumps(task.constraints, ensure_ascii=False),
                        task.status.value,
                        json.dumps(task.completed_items, ensure_ascii=False),
                        task.current_work,
                        json.dumps(task.remaining_items, ensure_ascii=False),
                        json.dumps(task.known_issues, ensure_ascii=False),
                        task.created_at.isoformat(),
                        task.updated_at.isoformat(),
                        json.dumps(task.metadata, ensure_ascii=False),
                    ),
                )
        except sqlite3.Error as err:
            raise DatabaseStateError(f"Failed to save task '{task.id}': {err}") from err

    def get_task(self, task_id: str) -> Task | None:
        """Retrieve a Task by its stable identifier."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, project_id, title, objective, requirements, constraints,
                       status, completed_items, current_work, remaining_items,
                       known_issues, created_at, updated_at, metadata
                FROM tasks WHERE id = ?;
                """,
                (task_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_task(row)
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            raise StateCorruptionError(f"Failed to load task '{task_id}': {err}") from err

    def list_tasks(self, project_id: str) -> list[Task]:
        """List all tasks associated with a given project ordered by creation time."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, project_id, title, objective, requirements, constraints,
                       status, completed_items, current_work, remaining_items,
                       known_issues, created_at, updated_at, metadata
                FROM tasks WHERE project_id = ?
                ORDER BY created_at ASC;
                """,
                (project_id,),
            )
            return [self._row_to_task(row) for row in cursor.fetchall()]
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            msg = f"Failed to list tasks for project '{project_id}': {err}"
            raise StateCorruptionError(msg) from err

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        """Convert a database row into a Task domain entity."""
        return Task(
            id=row["id"],
            project_id=row["project_id"],
            title=row["title"],
            objective=row["objective"],
            requirements=json.loads(row["requirements"]),
            constraints=json.loads(row["constraints"]),
            status=TaskStatus(row["status"]),
            completed_items=json.loads(row["completed_items"]),
            current_work=row["current_work"],
            remaining_items=json.loads(row["remaining_items"]),
            known_issues=json.loads(row["known_issues"]),
            created_at=_parse_utc_datetime(row["created_at"]),
            updated_at=_parse_utc_datetime(row["updated_at"]),
            metadata=json.loads(row["metadata"]),
        )

    # --- Runtime / Active Task Operations ---

    def get_active_task_id(self, project_id: str) -> str | None:
        """Retrieve the identifier of the active task for the project, if any."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                "SELECT active_task_id FROM project_runtime WHERE project_id = ?;",
                (project_id,),
            )
            row = cursor.fetchone()
            if row is None or row["active_task_id"] is None:
                return None
            return str(row["active_task_id"])
        except sqlite3.Error as err:
            msg = f"Failed to get active task for project '{project_id}': {err}"
            raise StateCorruptionError(msg) from err

    def set_active_task_id(self, project_id: str, task_id: str | None) -> None:
        """Set or clear the active task identifier for the project."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO project_runtime (project_id, active_task_id)
                    VALUES (?, ?)
                    ON CONFLICT(project_id) DO UPDATE SET active_task_id = excluded.active_task_id;
                    """,
                    (project_id, task_id),
                )
        except sqlite3.Error as err:
            raise DatabaseStateError(
                f"Failed to set active task '{task_id}' for project '{project_id}': {err}"
            ) from err

    # --- Git Snapshot Operations ---

    def save_snapshot(self, snapshot: GitSnapshot) -> None:
        """Persist a canonical GitSnapshot record."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO git_snapshots (
                        id, project_id, project_root, git_root, git_version,
                        branch, head_sha, detached_head, dirty,
                        staged_files, modified_files, untracked_files, conflicted_files,
                        working_tree_diff_summary, staged_diff_summary,
                        captured_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        snapshot.id,
                        snapshot.project_id,
                        snapshot.project_root,
                        snapshot.git_root,
                        snapshot.git_version,
                        snapshot.branch,
                        snapshot.head_sha,
                        1 if snapshot.detached_head else 0,
                        1 if snapshot.dirty else 0,
                        json.dumps(snapshot.staged_files),
                        json.dumps(snapshot.modified_files),
                        json.dumps(snapshot.untracked_files),
                        json.dumps(snapshot.conflicted_files),
                        snapshot.working_tree_diff_summary,
                        snapshot.staged_diff_summary,
                        snapshot.captured_at.isoformat(),
                        json.dumps(snapshot.metadata),
                    ),
                )
        except sqlite3.IntegrityError as err:
            raise DatabaseStateError(
                f"Failed to persist Git snapshot '{snapshot.id}': {err}"
            ) from err
        except sqlite3.Error as err:
            raise DatabaseStateError(f"Failed to save Git snapshot '{snapshot.id}': {err}") from err

    def get_snapshot(self, snapshot_id: str) -> GitSnapshot | None:
        """Retrieve a GitSnapshot by its identifier."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, project_id, project_root, git_root, git_version,
                       branch, head_sha, detached_head, dirty,
                       staged_files, modified_files, untracked_files, conflicted_files,
                       working_tree_diff_summary, staged_diff_summary,
                       captured_at, metadata
                FROM git_snapshots WHERE id = ?;
                """,
                (snapshot_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_snapshot(row)
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            msg = f"Failed to retrieve snapshot '{snapshot_id}': {err}"
            raise StateCorruptionError(msg) from err

    def list_snapshots(self, project_id: str, limit: int = 10) -> list[GitSnapshot]:
        """List snapshots for a given project, ordered newest first."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, project_id, project_root, git_root, git_version,
                       branch, head_sha, detached_head, dirty,
                       staged_files, modified_files, untracked_files, conflicted_files,
                       working_tree_diff_summary, staged_diff_summary,
                       captured_at, metadata
                FROM git_snapshots WHERE project_id = ?
                ORDER BY captured_at DESC
                LIMIT ?;
                """,
                (project_id, max(1, limit)),
            )
            return [self._row_to_snapshot(row) for row in cursor.fetchall()]
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            msg = f"Failed to list snapshots for project '{project_id}': {err}"
            raise StateCorruptionError(msg) from err

    def _row_to_snapshot(self, row: sqlite3.Row) -> GitSnapshot:
        """Convert a database row into a GitSnapshot domain entity."""
        return GitSnapshot(
            id=row["id"],
            project_id=row["project_id"],
            project_root=row["project_root"],
            git_root=row["git_root"],
            git_version=row["git_version"],
            branch=row["branch"],
            head_sha=row["head_sha"],
            detached_head=bool(row["detached_head"]),
            dirty=bool(row["dirty"]),
            staged_files=json.loads(row["staged_files"]),
            modified_files=json.loads(row["modified_files"]),
            untracked_files=json.loads(row["untracked_files"]),
            conflicted_files=json.loads(row["conflicted_files"]),
            working_tree_diff_summary=row["working_tree_diff_summary"],
            staged_diff_summary=row["staged_diff_summary"],
            captured_at=_parse_utc_datetime(row["captured_at"]),
            metadata=json.loads(row["metadata"]),
        )
