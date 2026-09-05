"""Real v4 state and transaction rollback tests for v5 invocation lineage."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite import migrations
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.errors import DatabaseStateError, UnsupportedSchemaVersionError
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session
from tests.factories import make_payload
from tests.unit.test_sqlite_v4_migrations import _build_v3_database


def build_v4(path: Path) -> dict[str, str]:
    ids = _build_v3_database(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("PRAGMA foreign_keys = ON")
        migrations._migrate_v4(conn)
        conn.execute("INSERT INTO schema_metadata VALUES (4, ?)", (utc_now().isoformat(),))
        payload = make_payload().model_copy(update={"task_id": ids["task"]})
        conn.execute(
            """INSERT INTO handoffs (
            id, protocol_version, project_id, task_id, source_session_id,
            source_provider_id, target_provider_id, git_snapshot_id, status, payload,
            created_at, metadata
        ) VALUES ('handoff_v4', 1, ?, ?, ?, 'claude', 'codex', ?, 'prepared', ?, ?, '{}')""",
            (
                ids["project"],
                ids["task"],
                ids["session"],
                ids["snapshot"],
                payload.model_dump_json(),
                utc_now().isoformat(),
            ),
        )
    return ids


def rows(path: Path) -> dict[str, list[tuple[object, ...]]]:
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


def test_v4_to_v5_preserves_all_state_and_nullable_legacy_identity(tmp_path: Path) -> None:
    path = tmp_path / "v4.sqlite3"
    ids = build_v4(path)
    before = rows(path)
    with SQLiteStateStore(path) as store:
        assert store.get_schema_version() == 5
        assert store.get_project(ids["project"]) is not None
        assert store.get_task(ids["task"]) is not None
        assert store.get_active_task_id(ids["project"]) == ids["task"]
        assert store.get_snapshot(ids["snapshot"]) is not None
        old = store.get_session(ids["session"])
        assert old is not None and old.native_session_id is None
        assert old.resumed_from_session_id is None
        assert store.get_handoff("handoff_v4") is not None
    after = rows(path)
    assert after.pop("sessions") == [(*row, None) for row in before.pop("sessions")]
    assert before == after
    with SQLiteStateStore(path) as store:
        assert store.migrate() == 5
    after_noop = rows(path)
    assert after_noop["sessions"][0][-1] is None


def test_v5_failed_migration_rolls_back_column_and_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "rollback.sqlite3"
    build_v4(path)
    before = rows(path)

    def broken(conn: sqlite3.Connection) -> None:
        migrations._migrate_v5(conn)
        conn.execute('UPDATE tasks SET title = "SHOULD ROLL BACK"')
        conn.execute("SELECT * FROM missing_table")

    monkeypatch.setattr(migrations, "MIGRATIONS", [*migrations.MIGRATIONS[:4], broken])
    with pytest.raises(DatabaseStateError):
        SQLiteStateStore(path)
    with closing(sqlite3.connect(path)) as conn, conn:
        assert migrations.get_current_schema_version(conn) == 4
        assert "resumed_from_session_id" not in [
            row[1] for row in conn.execute("PRAGMA table_info(sessions)")
        ]
    assert rows(path) == before


def test_v5_lineage_fk_survives_restart_and_parent_delete_sets_null(tmp_path: Path) -> None:
    path = tmp_path / "lineage.sqlite3"
    ids = build_v4(path)
    with SQLiteStateStore(path) as store:
        child = Session(
            task_id=ids["task"],
            provider_id=PROVIDER_CLAUDE,
            native_session_id="native-A",
            resumed_from_session_id=ids["session"],
        )
        store.save_session(child)
        with pytest.raises(DatabaseStateError):
            store.save_session(
                child.model_copy(update={"id": "bad", "resumed_from_session_id": "missing"})
            )
    with SQLiteStateStore(path) as store:
        assert store.get_session(child.id) == child
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM sessions WHERE id = ?", (ids["session"],))
    with SQLiteStateStore(path) as store:
        restored = store.get_session(child.id)
        assert restored is not None and restored.resumed_from_session_id is None
        assert restored.native_session_id == "native-A"


@pytest.mark.parametrize("start_version", [0, 1, 4, 5])
def test_migration_chain_and_noop(tmp_path: Path, start_version: int) -> None:
    path = tmp_path / "chain.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE schema_metadata (schema_version INTEGER PRIMARY KEY, "
            "applied_at TEXT NOT NULL)"
        )
        for n in range(start_version):
            migrations.MIGRATIONS[n](conn)
            conn.execute(
                "INSERT INTO schema_metadata VALUES (?, ?)", (n + 1, utc_now().isoformat())
            )
        conn.commit()
        assert migrations.run_migrations(conn) == 5
        assert [row[0] for row in conn.execute("SELECT schema_version FROM schema_metadata")] == [
            1,
            2,
            3,
            4,
            5,
        ]
        before = conn.total_changes
        assert migrations.run_migrations(conn) == 5
        assert conn.total_changes == before


def test_future_v999_rejected_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
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
