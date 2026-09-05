"""Unit tests for the SQLite HandoffStore persistence port implementation."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.domain.errors import DatabaseStateError
from cortexshift.domain.handoff import (
    HandoffFailureCode,
    HandoffRecord,
    HandoffStatus,
)
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.ports.handoff_store import HandoffStore
from tests.factories import make_payload, seed_project, seed_session


def _record(project_id: str, task_id: str, session_id: str, **overrides: object) -> HandoffRecord:
    defaults: dict[str, object] = {
        "project_id": project_id,
        "task_id": task_id,
        "source_session_id": session_id,
        "source_provider_id": PROVIDER_CLAUDE,
        "target_provider_id": PROVIDER_CODEX,
        "payload": make_payload(),
    }
    defaults.update(overrides)
    return HandoffRecord(**defaults)  # type: ignore[arg-type]


def test_sqlite_store_satisfies_handoff_store_port(tmp_path: Path) -> None:
    """Verify SQLiteStateStore structurally implements the HandoffStore port."""
    with SQLiteStateStore(tmp_path / "state.sqlite3") as store:
        assert isinstance(store, HandoffStore)


def test_save_and_get_handoff_roundtrip(tmp_path: Path) -> None:
    """Verify a persisted handoff reads back with a complete canonical payload."""
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    record = _record(project.id, task.id, session.id)
    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        store.save_handoff(record)
        loaded = store.get_handoff(record.id)

    assert loaded is not None
    assert loaded.id == record.id
    assert loaded.protocol_version == 1
    assert loaded.status == HandoffStatus.PREPARED
    assert loaded.source_provider_id == PROVIDER_CLAUDE
    assert loaded.target_provider_id == PROVIDER_CODEX
    assert loaded.payload == record.payload
    assert loaded.payload.git_state.branch == "main"
    assert loaded.delivered_at is None
    assert loaded.failure_code is None


def test_get_missing_handoff_returns_none(tmp_path: Path) -> None:
    """Verify an unknown handoff identifier resolves to None rather than raising."""
    seed_project(tmp_path)
    with SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3", auto_migrate=False) as store:
        assert store.get_handoff("handoff_missing") is None


def test_update_delivery_marks_delivered(tmp_path: Path) -> None:
    """Verify delivery metadata updates bind the target session and timestamp."""
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)
    target = seed_session(tmp_path, task.id, provider_id=PROVIDER_CODEX)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    record = _record(project.id, task.id, session.id)
    delivered_at = utc_now()
    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        store.save_handoff(record)
        store.update_handoff_delivery(
            record.id,
            status=HandoffStatus.DELIVERED,
            target_session_id=target.id,
            delivered_at=delivered_at,
        )
        loaded = store.get_handoff(record.id)

    assert loaded is not None
    assert loaded.status == HandoffStatus.DELIVERED
    assert loaded.target_session_id == target.id
    assert loaded.delivered_at is not None
    assert loaded.failure_code is None


def test_update_delivery_marks_failed_with_code(tmp_path: Path) -> None:
    """Verify failed delivery records a safe machine classification."""
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    record = _record(project.id, task.id, session.id, target_provider_id=PROVIDER_ANTIGRAVITY)
    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        store.save_handoff(record)
        store.update_handoff_delivery(
            record.id,
            status=HandoffStatus.FAILED,
            failure_code=HandoffFailureCode.BOOTSTRAP_TIMEOUT,
        )
        loaded = store.get_handoff(record.id)

    assert loaded is not None
    assert loaded.status == HandoffStatus.FAILED
    assert loaded.failure_code == HandoffFailureCode.BOOTSTRAP_TIMEOUT
    assert loaded.delivered_at is None


def test_list_handoffs_orders_newest_first_and_filters(tmp_path: Path) -> None:
    """Verify listing is newest-first and scoped by project and task."""
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        first = _record(project.id, task.id, session.id)
        second = _record(
            project.id,
            task.id,
            session.id,
            target_provider_id=PROVIDER_ANTIGRAVITY,
            created_at=utc_now(),
        )
        store.save_handoff(first)
        store.save_handoff(second)

        listed = store.list_handoffs(project_id=project.id)
        assert [h.id for h in listed] == [second.id, first.id]

        assert len(store.list_handoffs(project_id=project.id, limit=1)) == 1
        assert store.list_handoffs(project_id=project.id, task_id="task_other") == []
        assert store.list_handoffs(project_id="proj_other") == []


def test_save_handoff_is_idempotent_on_id(tmp_path: Path) -> None:
    """Verify re-saving the same handoff updates rather than duplicating it."""
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    record = _record(project.id, task.id, session.id)
    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        store.save_handoff(record)
        store.save_handoff(record.mark_delivered(target_session_id=None))

        assert len(store.list_handoffs(project_id=project.id)) == 1
        loaded = store.get_handoff(record.id)
        assert loaded is not None
        assert loaded.status == HandoffStatus.DELIVERED


def test_handoff_foreign_keys_are_enforced(tmp_path: Path) -> None:
    """Verify a handoff cannot reference a non-existent project, task, or session."""
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        with pytest.raises(DatabaseStateError):
            store.save_handoff(_record("proj_ghost", task.id, session.id))
        with pytest.raises(DatabaseStateError):
            store.save_handoff(_record(project.id, "task_ghost", session.id))
        with pytest.raises(DatabaseStateError):
            store.save_handoff(_record(project.id, task.id, "sess_ghost"))
