"""Unit tests for SQLite schema v3 to v4 migration regression."""

import sqlite3
from pathlib import Path

from cortexshift.adapters.sqlite.migrations import (
    CURRENT_SCHEMA_VERSION,
    _migrate_v1,
    _migrate_v2,
    _migrate_v3,
    get_current_schema_version,
    run_migrations,
)
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.handoff import HandoffStatus
from cortexshift.domain.identifiers import generate_id, utc_now
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import SessionStatus
from cortexshift.domain.task import TaskStatus
from tests.factories import make_payload


def _build_v3_database(db_file: Path) -> dict[str, str]:
    """Create a genuine schema-v3 database populated with Phase 1-4 state."""
    conn = sqlite3.connect(str(db_file))
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
    _migrate_v3(conn)

    now_iso = utc_now().isoformat()
    conn.execute(
        "INSERT INTO schema_metadata (schema_version, applied_at) VALUES (1, ?), (2, ?), (3, ?);",
        (now_iso, now_iso, now_iso),
    )

    ids = {
        "project": generate_id("proj"),
        "task": generate_id("task"),
        "snapshot": generate_id("snap"),
        "session": generate_id("sess"),
    }

    conn.execute(
        """
        INSERT INTO projects (id, name, repo_path, created_at, metadata)
        VALUES (?, 'V3Project', '/path/to/v3', ?, '{}');
        """,
        (ids["project"], now_iso),
    )
    conn.execute(
        """
        INSERT INTO tasks (
            id, project_id, title, objective, requirements, constraints,
            status, completed_items, current_work, remaining_items,
            known_issues, created_at, updated_at, metadata
        ) VALUES (
            ?, ?, 'V3 Task', 'Objective for V3', '["reqA"]', '["conA"]',
            'in_progress', '["done1"]', 'active work', '["todo1"]',
            '["issue1"]', ?, ?, '{}'
        );
        """,
        (ids["task"], ids["project"], now_iso, now_iso),
    )
    conn.execute(
        "INSERT INTO project_runtime (project_id, active_task_id) VALUES (?, ?);",
        (ids["project"], ids["task"]),
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
            ?, ?, '/path/to/v3', '/path/to/v3', 'git version 2.40.0',
            'feature-branch', 'abc12345', 0, 1,
            '["staged.py"]', '["mod.py"]', '["untr.py"]', '[]',
            '1 file changed', '1 file staged',
            ?, '{}'
        );
        """,
        (ids["snapshot"], ids["project"], now_iso),
    )
    conn.execute(
        """
        INSERT INTO sessions (
            id, task_id, provider_id, native_session_id, status,
            started_at, ended_at, exit_reason, exit_code, metadata
        ) VALUES (?, ?, 'claude', NULL, 'completed', ?, ?, 'normal_completion', 0, '{}');
        """,
        (ids["session"], ids["task"], now_iso, now_iso),
    )
    conn.commit()
    assert get_current_schema_version(conn) == 3
    conn.close()
    return ids


def test_migrate_v3_to_v4_preserves_all_prior_state(tmp_path: Path) -> None:
    """Verify genuine schema-v3 state survives the Phase 5 migration to v4 intact."""
    db_file = tmp_path / "test_v3.sqlite3"
    ids = _build_v3_database(db_file)

    with SQLiteStateStore(db_file, auto_migrate=True) as store:
        assert store.get_schema_version() == 4
        assert store.get_schema_version() == CURRENT_SCHEMA_VERSION

        project = store.get_project(ids["project"])
        assert project is not None
        assert project.name == "V3Project"

        task = store.get_task(ids["task"])
        assert task is not None
        assert task.title == "V3 Task"
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.requirements == ["reqA"]
        assert task.completed_items == ["done1"]

        assert store.get_active_task_id(ids["project"]) == ids["task"]

        snapshot = store.get_snapshot(ids["snapshot"])
        assert snapshot is not None
        assert snapshot.branch == "feature-branch"
        assert snapshot.staged_files == ["staged.py"]

        session = store.get_session(ids["session"])
        assert session is not None
        assert session.provider_id == PROVIDER_CLAUDE
        assert session.status == SessionStatus.COMPLETED

        assert store.list_handoffs(project_id=ids["project"]) == []


def test_v4_handoffs_table_exists_with_expected_columns(tmp_path: Path) -> None:
    """Verify the v4 migration creates the handoffs table and its indexes."""
    db_file = tmp_path / "test_v3_cols.sqlite3"
    _build_v3_database(db_file)

    with SQLiteStateStore(db_file, auto_migrate=True):
        pass

    conn = sqlite3.connect(str(db_file))
    try:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        }
        assert "handoffs" in tables

        columns = {row[1] for row in conn.execute("PRAGMA table_info(handoffs);")}
        assert columns == {
            "id",
            "protocol_version",
            "project_id",
            "task_id",
            "source_session_id",
            "source_provider_id",
            "target_provider_id",
            "git_snapshot_id",
            "target_session_id",
            "status",
            "payload",
            "created_at",
            "delivered_at",
            "failure_code",
            "metadata",
        }

        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='handoffs';"
            )
        }
        assert "idx_handoffs_project_id" in indexes
        assert "idx_handoffs_task_id" in indexes
        assert "idx_handoffs_created_at" in indexes
    finally:
        conn.close()


def test_v4_schema_stores_no_credential_or_prompt_columns(tmp_path: Path) -> None:
    """Verify the handoffs table has no columns for secrets or rendered prompts."""
    db_file = tmp_path / "test_v3_privacy.sqlite3"
    _build_v3_database(db_file)
    with SQLiteStateStore(db_file, auto_migrate=True):
        pass

    conn = sqlite3.connect(str(db_file))
    try:
        columns = {row[1].lower() for row in conn.execute("PRAGMA table_info(handoffs);")}
    finally:
        conn.close()

    forbidden = {
        "token",
        "api_key",
        "apikey",
        "secret",
        "password",
        "credential",
        "prompt",
        "rendered_prompt",
        "transcript",
        "response",
        "bootstrap_response",
        "diff",
        "remote_url",
    }
    assert columns & forbidden == set()


def test_full_migration_chain_v1_to_v4(tmp_path: Path) -> None:
    """Verify a v1 database migrates through every intermediate version to v4."""
    db_file = tmp_path / "chain.sqlite3"
    conn = sqlite3.connect(str(db_file))
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
    conn.commit()
    assert get_current_schema_version(conn) == 1

    assert run_migrations(conn) == 4
    versions = {row[0] for row in conn.execute("SELECT schema_version FROM schema_metadata;")}
    assert versions == {1, 2, 3, 4}
    conn.close()


def test_v4_migration_is_idempotent(tmp_path: Path) -> None:
    """Verify reopening an already-v4 database applies no further migration."""
    db_file = tmp_path / "noop.sqlite3"
    with SQLiteStateStore(db_file, auto_migrate=True) as store:
        assert store.get_schema_version() == 4

    with SQLiteStateStore(db_file, auto_migrate=True) as store:
        assert store.get_schema_version() == 4
        assert store.migrate() == 4


def test_handoff_survives_migrated_database(tmp_path: Path) -> None:
    """Verify a handoff written after migration reads back with a complete payload."""
    db_file = tmp_path / "post_migration.sqlite3"
    ids = _build_v3_database(db_file)

    from cortexshift.domain.handoff import HandoffRecord

    with SQLiteStateStore(db_file, auto_migrate=True) as store:
        record = HandoffRecord(
            project_id=ids["project"],
            task_id=ids["task"],
            source_session_id=ids["session"],
            source_provider_id=PROVIDER_CLAUDE,
            target_provider_id=PROVIDER_CODEX,
            git_snapshot_id=ids["snapshot"],
            payload=make_payload(),
        )
        store.save_handoff(record)

    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        loaded = store.get_handoff(record.id)
        assert loaded is not None
        assert loaded.status == HandoffStatus.PREPARED
        assert loaded.payload.original_objective == record.payload.original_objective
        assert loaded.git_snapshot_id == ids["snapshot"]
