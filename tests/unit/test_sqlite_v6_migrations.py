"""Unit tests for SQLite schema v5 to v6 migration regression and Checkpoint storage."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite import migrations
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.errors import DatabaseStateError, UnsupportedSchemaVersionError
from cortexshift.domain.identifiers import generate_id, utc_now
from tests.unit.test_sqlite_v5_migrations import build_v4


def build_v5(path: Path) -> dict[str, str]:
    """Create a genuine schema-v5 database populated with Phase 1-6 state."""
    ids = build_v4(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("PRAGMA foreign_keys = ON")
        migrations._migrate_v5(conn)
        conn.execute("INSERT INTO schema_metadata VALUES (5, ?)", (utc_now().isoformat(),))

        resumed_sess_id = generate_id("sess")
        now_iso = utc_now().isoformat()
        conn.execute(
            """
            INSERT INTO sessions (
                id, task_id, provider_id, native_session_id, status,
                started_at, ended_at, exit_reason, exit_code, metadata,
                resumed_from_session_id
            ) VALUES (
                ?, ?, 'codex', 'native-session-v5', 'completed', ?, ?,
                'normal_completion', 0, '{}', ?
            );
            """,
            (resumed_sess_id, ids["task"], now_iso, now_iso, ids["session"]),
        )
        ids["resumed_session"] = resumed_sess_id
    return ids


def rows_v6(path: Path) -> dict[str, list[tuple[object, ...]]]:
    with closing(sqlite3.connect(path)) as conn, conn:
        return {
            table: conn.execute("SELECT * FROM " + table).fetchall()
            for table in [
                "projects",
                "tasks",
                "project_runtime",
                "git_snapshots",
                "sessions",
                "handoffs",
            ]
        }


def test_v5_to_v6_preserves_all_state_and_adds_checkpoint_structures(tmp_path: Path) -> None:
    path = tmp_path / "v5.sqlite3"
    ids = build_v5(path)
    before = rows_v6(path)

    with SQLiteStateStore(path) as store:
        assert store.get_schema_version() == 6
        assert store.get_project(ids["project"]) is not None
        assert store.get_task(ids["task"]) is not None
        assert store.get_active_task_id(ids["project"]) == ids["task"]
        assert store.get_snapshot(ids["snapshot"]) is not None

        old_session = store.get_session(ids["session"])
        assert old_session is not None
        assert old_session.reconciled_at is None
        assert old_session.resumed_from_session_id is None

        resumed_session = store.get_session(ids["resumed_session"])
        assert resumed_session is not None
        assert resumed_session.resumed_from_session_id == ids["session"]
        assert resumed_session.reconciled_at is None

        old_handoff = store.get_handoff("handoff_v4")
        assert old_handoff is not None
        assert old_handoff.source_checkpoint_id is None

        assert store.list_checkpoints(task_id=ids["task"]) == []

    after = rows_v6(path)
    assert after.pop("sessions") == [(*row, None) for row in before.pop("sessions")]
    assert after.pop("handoffs") == [(*row, None) for row in before.pop("handoffs")]
    assert before == after

    with SQLiteStateStore(path) as store:
        assert store.migrate() == 6


def test_v6_checkpoints_table_exists_with_expected_columns_and_indexes(tmp_path: Path) -> None:
    db_file = tmp_path / "test_v6_cols.sqlite3"
    build_v5(db_file)

    with SQLiteStateStore(db_file, auto_migrate=True):
        pass

    with closing(sqlite3.connect(str(db_file))) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        }
        assert "checkpoints" in tables

        columns = {row[1] for row in conn.execute("PRAGMA table_info(checkpoints);")}
        assert columns == {
            "id",
            "protocol_version",
            "project_id",
            "task_id",
            "session_id",
            "git_snapshot_id",
            "kind",
            "payload",
            "created_at",
            "metadata",
        }

        indexes = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='checkpoints';"
            )
        }
        assert "idx_checkpoints_project_id" in indexes
        assert "idx_checkpoints_task_id" in indexes
        assert "idx_checkpoints_created_at" in indexes
        assert "idx_checkpoints_session_id" in indexes


def test_v6_schema_stores_no_credentials_or_transcripts(tmp_path: Path) -> None:
    db_file = tmp_path / "test_v6_privacy.sqlite3"
    build_v5(db_file)
    with SQLiteStateStore(db_file, auto_migrate=True):
        pass

    forbidden = {
        "token",
        "api_key",
        "apikey",
        "secret",
        "password",
        "credential",
        "transcript",
        "reasoning",
        "prompt",
        "rendered_prompt",
        "diff",
    }

    with closing(sqlite3.connect(str(db_file))) as conn:
        for table in ["checkpoints", "sessions", "handoffs"]:
            columns = {row[1].lower() for row in conn.execute(f"PRAGMA table_info({table});")}
            assert columns & forbidden == set(), f"Forbidden columns found in {table}"


def test_full_migration_chain_v1_to_v6(tmp_path: Path) -> None:
    db_file = tmp_path / "chain_v6.sqlite3"
    with closing(sqlite3.connect(str(db_file))) as conn, conn:
        conn.execute(
            """
            CREATE TABLE schema_metadata (
                schema_version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            """
        )
        migrations._migrate_v1(conn)
        conn.execute(
            "INSERT INTO schema_metadata (schema_version, applied_at) VALUES (1, ?);",
            (utc_now().isoformat(),),
        )
        conn.commit()

        assert migrations.run_migrations(conn) == 6
        versions = [row[0] for row in conn.execute("SELECT schema_version FROM schema_metadata")]
        assert versions == [1, 2, 3, 4, 5, 6]


def test_v6_migration_is_idempotent(tmp_path: Path) -> None:
    db_file = tmp_path / "noop_v6.sqlite3"
    with SQLiteStateStore(db_file, auto_migrate=True) as store:
        assert store.get_schema_version() == 6

    with SQLiteStateStore(db_file, auto_migrate=True) as store:
        assert store.get_schema_version() == 6
        assert store.migrate() == 6


def test_v6_failed_migration_rolls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "rollback_v6.sqlite3"
    build_v5(path)
    before = rows_v6(path)

    def broken(conn: sqlite3.Connection) -> None:
        migrations._migrate_v6(conn)
        conn.execute("UPDATE tasks SET title = 'SHOULD ROLL BACK'")
        conn.execute("SELECT * FROM non_existent_table")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*migrations.MIGRATIONS[:5], broken])
    with pytest.raises(DatabaseStateError):
        SQLiteStateStore(path)

    with closing(sqlite3.connect(path)) as conn, conn:
        assert migrations.get_current_schema_version(conn) == 5
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
        }
        assert "checkpoints" not in tables
        session_cols = [row[1] for row in conn.execute("PRAGMA table_info(sessions)")]
        assert "reconciled_at" not in session_cols
        handoff_cols = [row[1] for row in conn.execute("PRAGMA table_info(handoffs)")]
        assert "source_checkpoint_id" not in handoff_cols

    assert rows_v6(path) == before


def test_future_v999_rejected_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "future_v999.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE schema_metadata (schema_version INTEGER PRIMARY KEY, "
            "applied_at TEXT NOT NULL)"
        )
        conn.execute("INSERT INTO schema_metadata VALUES (999, ?)", (utc_now().isoformat(),))
        conn.commit()
        with pytest.raises(UnsupportedSchemaVersionError):
            migrations.run_migrations(conn)
        assert migrations.get_current_schema_version(conn) == 999
