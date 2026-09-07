"""SQLite implementation of the StateStore persistence port.

**Ordering contract.** Every listing that means "newest first" orders by its timestamp
descending and breaks ties on `rowid` descending: *a newer timestamp wins, and when two
records carry the same timestamp the later-persisted one wins.* Ascending listings state
the same rule in the other direction.

The tie-break is not cosmetic. `list_sessions` decides which session a handoff is built
from, which native conversation `resume` reattaches to, and which session a recovery
checkpoint is bound to; `list_checkpoints` and `list_handoffs` feed the "latest" lookups
those paths read. Timestamps come from `datetime.now(UTC)`, whose resolution is coarse
enough on some platforms -- roughly 16 ms on Windows -- that two records written in one
burst genuinely share an instant. Without a tie-break SQLite is free to return either
first, so CortexShift could hand off from the wrong session or bind a checkpoint to one.

`rowid` is the right tie-break here: every table in this schema is an ordinary rowid
table, saves are upserts that keep a row's original `rowid`, so it records the order in
which records were first persisted -- the chronology the timestamps were reaching for.
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cortexshift.adapters.sqlite.migrations import get_current_schema_version, run_migrations
from cortexshift.domain.checkpoint import (
    CheckpointKind,
    CheckpointPayload,
    CheckpointRecord,
)
from cortexshift.domain.errors import (
    DatabaseStateError,
    StateCorruptionError,
    UnsupportedSchemaVersionError,
)
from cortexshift.domain.git import GitSnapshot
from cortexshift.domain.handoff import (
    HandoffFailureCode,
    HandoffPayload,
    HandoffRecord,
    HandoffStatus,
)
from cortexshift.domain.project import Project
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task, TaskStatus
from cortexshift.ports.checkpoint_store import CheckpointStore
from cortexshift.ports.handoff_store import HandoffStore
from cortexshift.ports.repository import RepositorySnapshotStore
from cortexshift.ports.session_store import SessionStore
from cortexshift.ports.state_store import StateStore


def _parse_utc_datetime(iso_str: str) -> datetime:
    """Parse an ISO 8601 string and ensure it has timezone-aware UTC tzinfo."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class SQLiteStateStore(
    StateStore,
    RepositorySnapshotStore,
    SessionStore,
    HandoffStore,
    CheckpointStore,
):
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
                check_same_thread=False,
            )
            self._conn.row_factory = sqlite3.Row
            self._configure_connection()
        except sqlite3.Error as err:
            msg = f"Failed to open SQLite database at {self.db_path}: {err}"
            raise DatabaseStateError(msg) from err

        if auto_migrate:
            try:
                self.migrate()
            except BaseException:
                # A failed constructor has no context-manager exit to release the connection.
                self.close()
                raise

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
                ORDER BY created_at ASC, rowid ASC;
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
                ORDER BY captured_at DESC, rowid DESC
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

    # --- Session Operations (SessionStore) ---

    def save_session(self, session: Session) -> None:
        """Persist or update an agent execution Session."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO sessions (
                        id, task_id, provider_id, native_session_id, status,
                        started_at, ended_at, exit_reason, exit_code, metadata,
                        resumed_from_session_id, reconciled_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        task_id = excluded.task_id,
                        provider_id = excluded.provider_id,
                        native_session_id = excluded.native_session_id,
                        resumed_from_session_id = excluded.resumed_from_session_id,
                        status = excluded.status,
                        started_at = excluded.started_at,
                        ended_at = excluded.ended_at,
                        exit_reason = excluded.exit_reason,
                        exit_code = excluded.exit_code,
                        metadata = excluded.metadata,
                        reconciled_at = excluded.reconciled_at;
                    """,
                    (
                        session.id,
                        session.task_id,
                        str(session.provider_id),
                        session.native_session_id,
                        session.status.value,
                        session.started_at.isoformat(),
                        session.ended_at.isoformat() if session.ended_at else None,
                        session.exit_reason.value if session.exit_reason else None,
                        session.exit_code,
                        json.dumps(session.metadata),
                        session.resumed_from_session_id,
                        session.reconciled_at.isoformat() if session.reconciled_at else None,
                    ),
                )
        except sqlite3.IntegrityError as err:
            raise DatabaseStateError(f"Failed to persist session '{session.id}': {err}") from err
        except sqlite3.Error as err:
            raise DatabaseStateError(f"Failed to save session '{session.id}': {err}") from err

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a Session by its unique identifier."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, task_id, provider_id, native_session_id, status,
                       started_at, ended_at, exit_reason, exit_code, metadata,
                       resumed_from_session_id, reconciled_at
                FROM sessions WHERE id = ?;
                """,
                (session_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_session(row)
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            msg = f"Failed to retrieve session '{session_id}': {err}"
            raise StateCorruptionError(msg) from err

    def list_sessions(
        self,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int | None = 20,
    ) -> list[Session]:
        """List sessions, ordered newest first."""
        try:
            cursor = self._conn.cursor()
            conditions: list[str] = []
            params: list[Any] = []

            if task_id is not None:
                conditions.append("s.task_id = ?")
                params.append(task_id)

            if project_id is not None:
                conditions.append("t.project_id = ?")
                params.append(project_id)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"""
                SELECT s.id, s.task_id, s.provider_id, s.native_session_id, s.status,
                       s.started_at, s.ended_at, s.exit_reason, s.exit_code, s.metadata,
                       s.resumed_from_session_id, s.reconciled_at
                FROM sessions s
                JOIN tasks t ON s.task_id = t.id
                {where_clause}
                ORDER BY s.started_at DESC, s.rowid DESC
                LIMIT ?;
            """
            params.append(-1 if limit is None else max(1, limit))
            cursor.execute(query, params)
            return [self._row_to_session(row) for row in cursor.fetchall()]
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            msg = f"Failed to list sessions: {err}"
            raise StateCorruptionError(msg) from err

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        """Convert a database row into a Session domain entity."""
        return Session(
            id=row["id"],
            task_id=row["task_id"],
            provider_id=ProviderId(row["provider_id"]),
            native_session_id=row["native_session_id"],
            resumed_from_session_id=row["resumed_from_session_id"],
            status=SessionStatus(row["status"]),
            started_at=_parse_utc_datetime(row["started_at"]),
            ended_at=_parse_utc_datetime(row["ended_at"]) if row["ended_at"] else None,
            exit_reason=SessionExitReason(row["exit_reason"]) if row["exit_reason"] else None,
            exit_code=row["exit_code"],
            reconciled_at=(
                _parse_utc_datetime(row["reconciled_at"])
                if ("reconciled_at" in tuple(row.keys()) and row["reconciled_at"])
                else None
            ),
            metadata=json.loads(row["metadata"]),
        )

    # --- Handoff Operations (HandoffStore) ---

    def save_handoff(self, handoff: HandoffRecord) -> None:
        """Persist or update a canonical HandoffRecord.

        The canonical payload is stored as validated JSON text. Rendered provider
        prompts and provider responses are never persisted.
        """
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO handoffs (
                        id, protocol_version, project_id, task_id,
                        source_session_id, source_provider_id, target_provider_id,
                        git_snapshot_id, target_session_id, status, payload,
                        created_at, delivered_at, failure_code, metadata,
                        source_checkpoint_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        protocol_version = excluded.protocol_version,
                        project_id = excluded.project_id,
                        task_id = excluded.task_id,
                        source_session_id = excluded.source_session_id,
                        source_provider_id = excluded.source_provider_id,
                        target_provider_id = excluded.target_provider_id,
                        git_snapshot_id = excluded.git_snapshot_id,
                        target_session_id = excluded.target_session_id,
                        status = excluded.status,
                        payload = excluded.payload,
                        delivered_at = excluded.delivered_at,
                        failure_code = excluded.failure_code,
                        metadata = excluded.metadata,
                        source_checkpoint_id = excluded.source_checkpoint_id;
                    """,
                    (
                        handoff.id,
                        handoff.protocol_version,
                        handoff.project_id,
                        handoff.task_id,
                        handoff.source_session_id,
                        str(handoff.source_provider_id),
                        str(handoff.target_provider_id),
                        handoff.git_snapshot_id,
                        handoff.target_session_id,
                        handoff.status.value,
                        handoff.payload.model_dump_json(),
                        handoff.created_at.isoformat(),
                        handoff.delivered_at.isoformat() if handoff.delivered_at else None,
                        handoff.failure_code.value if handoff.failure_code else None,
                        json.dumps(handoff.metadata, ensure_ascii=False),
                        handoff.source_checkpoint_id,
                    ),
                )
        except sqlite3.IntegrityError as err:
            raise DatabaseStateError(f"Failed to persist handoff '{handoff.id}': {err}") from err
        except sqlite3.Error as err:
            raise DatabaseStateError(f"Failed to save handoff '{handoff.id}': {err}") from err

    def update_handoff_delivery(
        self,
        handoff_id: str,
        status: HandoffStatus,
        target_session_id: str | None = None,
        delivered_at: datetime | None = None,
        failure_code: HandoffFailureCode | None = None,
    ) -> None:
        """Update delivery metadata of an existing handoff record."""
        try:
            with self._conn:
                self._conn.execute(
                    """
                    UPDATE handoffs SET
                        status = ?,
                        target_session_id = COALESCE(?, target_session_id),
                        delivered_at = ?,
                        failure_code = ?
                    WHERE id = ?;
                    """,
                    (
                        status.value,
                        target_session_id,
                        delivered_at.isoformat() if delivered_at else None,
                        failure_code.value if failure_code else None,
                        handoff_id,
                    ),
                )
        except sqlite3.Error as err:
            msg = f"Failed to update handoff delivery for '{handoff_id}': {err}"
            raise DatabaseStateError(msg) from err

    def get_handoff(self, handoff_id: str) -> HandoffRecord | None:
        """Retrieve a HandoffRecord by its stable identifier."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, protocol_version, project_id, task_id,
                       source_session_id, source_provider_id, target_provider_id,
                       git_snapshot_id, target_session_id, status, payload,
                       created_at, delivered_at, failure_code, metadata,
                       source_checkpoint_id
                FROM handoffs WHERE id = ?;
                """,
                (handoff_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_handoff(row)
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            msg = f"Failed to retrieve handoff '{handoff_id}': {err}"
            raise StateCorruptionError(msg) from err

    def list_handoffs(
        self,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int = 20,
    ) -> list[HandoffRecord]:
        """List handoff records, ordered newest first."""
        try:
            cursor = self._conn.cursor()
            conditions: list[str] = []
            params: list[Any] = []

            if project_id is not None:
                conditions.append("project_id = ?")
                params.append(project_id)

            if task_id is not None:
                conditions.append("task_id = ?")
                params.append(task_id)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"""
                SELECT id, protocol_version, project_id, task_id,
                       source_session_id, source_provider_id, target_provider_id,
                       git_snapshot_id, target_session_id, status, payload,
                       created_at, delivered_at, failure_code, metadata,
                       source_checkpoint_id
                FROM handoffs
                {where_clause}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?;
            """
            params.append(max(1, limit))
            cursor.execute(query, params)
            return [self._row_to_handoff(row) for row in cursor.fetchall()]
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            raise StateCorruptionError(f"Failed to list handoffs: {err}") from err

    def _row_to_handoff(self, row: sqlite3.Row) -> HandoffRecord:
        """Convert a database row into a HandoffRecord domain entity."""
        return HandoffRecord(
            id=row["id"],
            protocol_version=int(row["protocol_version"]),
            project_id=row["project_id"],
            task_id=row["task_id"],
            source_session_id=row["source_session_id"],
            source_provider_id=ProviderId(row["source_provider_id"]),
            target_provider_id=ProviderId(row["target_provider_id"]),
            source_checkpoint_id=(
                row["source_checkpoint_id"]
                if ("source_checkpoint_id" in tuple(row.keys()) and row["source_checkpoint_id"])
                else None
            ),
            git_snapshot_id=row["git_snapshot_id"],
            target_session_id=row["target_session_id"],
            status=HandoffStatus(row["status"]),
            payload=HandoffPayload.model_validate_json(row["payload"]),
            created_at=_parse_utc_datetime(row["created_at"]),
            delivered_at=_parse_utc_datetime(row["delivered_at"]) if row["delivered_at"] else None,
            failure_code=(HandoffFailureCode(row["failure_code"]) if row["failure_code"] else None),
            metadata=json.loads(row["metadata"]),
        )

    # --- Checkpoint Operations (CheckpointStore) ---

    def save_checkpoint(self, checkpoint: CheckpointRecord) -> None:
        """Persist a canonical CheckpointRecord.

        The canonical payload is stored as validated JSON text. Rendered prompts,
        provider responses, transcripts, and full diffs are never persisted.
        """
        try:
            with self._conn:
                self._conn.execute(
                    """
                    INSERT INTO checkpoints (
                        id, protocol_version, project_id, task_id, session_id,
                        git_snapshot_id, kind, payload, created_at, metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        protocol_version = excluded.protocol_version,
                        project_id = excluded.project_id,
                        task_id = excluded.task_id,
                        session_id = excluded.session_id,
                        git_snapshot_id = excluded.git_snapshot_id,
                        kind = excluded.kind,
                        payload = excluded.payload,
                        created_at = excluded.created_at,
                        metadata = excluded.metadata;
                    """,
                    (
                        checkpoint.id,
                        checkpoint.protocol_version,
                        checkpoint.project_id,
                        checkpoint.task_id,
                        checkpoint.session_id,
                        checkpoint.git_snapshot_id,
                        checkpoint.kind.value,
                        checkpoint.payload.model_dump_json(),
                        checkpoint.created_at.isoformat(),
                        json.dumps(checkpoint.metadata, ensure_ascii=False),
                    ),
                )
        except sqlite3.IntegrityError as err:
            raise DatabaseStateError(
                f"Failed to persist checkpoint '{checkpoint.id}': {err}"
            ) from err
        except sqlite3.Error as err:
            raise DatabaseStateError(f"Failed to save checkpoint '{checkpoint.id}': {err}") from err

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord | None:
        """Retrieve a CheckpointRecord by its stable identifier."""
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, protocol_version, project_id, task_id, session_id,
                       git_snapshot_id, kind, payload, created_at, metadata
                FROM checkpoints WHERE id = ?;
                """,
                (checkpoint_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_checkpoint(row)
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            msg = f"Failed to retrieve checkpoint '{checkpoint_id}': {err}"
            raise StateCorruptionError(msg) from err

    def list_checkpoints(
        self,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int | None = 20,
    ) -> list[CheckpointRecord]:
        """List checkpoint records, ordered newest first."""
        try:
            cursor = self._conn.cursor()
            conditions: list[str] = []
            params: list[Any] = []

            if project_id is not None:
                conditions.append("project_id = ?")
                params.append(project_id)

            if task_id is not None:
                conditions.append("task_id = ?")
                params.append(task_id)

            where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
            query = f"""
                SELECT id, protocol_version, project_id, task_id, session_id,
                       git_snapshot_id, kind, payload, created_at, metadata
                FROM checkpoints
                {where_clause}
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?;
            """
            params.append(-1 if limit is None else max(1, limit))
            cursor.execute(query, params)
            return [self._row_to_checkpoint(row) for row in cursor.fetchall()]
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            raise StateCorruptionError(f"Failed to list checkpoints: {err}") from err

    def get_latest_checkpoint(self, task_id: str) -> CheckpointRecord | None:
        """Retrieve the newest checkpoint record for a given task."""
        results = self.list_checkpoints(task_id=task_id, limit=1)
        return results[0] if results else None

    def list_task_checkpoint_history(self, task_id: str) -> list[CheckpointRecord]:
        """List every checkpoint for one task in deterministic chronological order.

        Ordered oldest first by `created_at ASC, rowid ASC`. The rowid tie-breaker keeps
        the sequence stable when two checkpoints share an identical timestamp, which
        coarse clock granularity makes reachable on Windows. `task_id` is mandatory and
        applied in SQL, so this query can never span tasks.
        """
        try:
            cursor = self._conn.cursor()
            cursor.execute(
                """
                SELECT id, protocol_version, project_id, task_id, session_id,
                       git_snapshot_id, kind, payload, created_at, metadata
                FROM checkpoints
                WHERE task_id = ?
                ORDER BY created_at ASC, rowid ASC;
                """,
                (task_id,),
            )
            return [self._row_to_checkpoint(row) for row in cursor.fetchall()]
        except (sqlite3.Error, ValueError, json.JSONDecodeError) as err:
            raise StateCorruptionError(
                f"Failed to list checkpoint history for task '{task_id}': {err}"
            ) from err

    def _row_to_checkpoint(self, row: sqlite3.Row) -> CheckpointRecord:
        """Convert a database row into a CheckpointRecord domain entity."""
        return CheckpointRecord(
            id=row["id"],
            protocol_version=int(row["protocol_version"]),
            project_id=row["project_id"],
            task_id=row["task_id"],
            session_id=row["session_id"],
            git_snapshot_id=row["git_snapshot_id"],
            kind=CheckpointKind(row["kind"]),
            payload=CheckpointPayload.model_validate_json(row["payload"]),
            created_at=_parse_utc_datetime(row["created_at"]),
            metadata=json.loads(row["metadata"]),
        )
