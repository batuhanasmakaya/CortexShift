"""Unit tests for SQLite schema migration runner."""

import sqlite3
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.migrations import (
    CURRENT_SCHEMA_VERSION,
    get_current_schema_version,
    run_migrations,
)
from cortexshift.domain.errors import DatabaseStateError, UnsupportedSchemaVersionError


def test_fresh_database_migrates_to_v1(tmp_path: Path) -> None:
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
    finally:
        conn.close()


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
