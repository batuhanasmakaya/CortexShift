"""Unit tests for handoff source session selection."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.source_session import select_source_session
from cortexshift.domain.errors import (
    NoSourceSessionError,
    SessionNotFoundError,
    SessionTaskMismatchError,
)
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from tests.factories import seed_project, seed_session


def _store(tmp_path: Path) -> SQLiteStateStore:
    return SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3", auto_migrate=False)


def test_no_prior_session_raises(tmp_path: Path) -> None:
    """Verify switch never silently reinterprets itself as a first-agent run."""
    _, task = seed_project(tmp_path)
    with _store(tmp_path) as store, pytest.raises(NoSourceSessionError) as exc:
        select_source_session(store, task)

    assert "cortexshift run <provider>" in str(exc.value)


def test_selects_most_recent_session(tmp_path: Path) -> None:
    """Verify the newest session on the active task is chosen by default."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)
    newest = seed_session(tmp_path, task.id, provider_id=PROVIDER_CODEX)

    with _store(tmp_path) as store:
        assert select_source_session(store, task).id == newest.id


def test_spawn_failed_session_is_not_preferred(tmp_path: Path) -> None:
    """Verify a session whose process never spawned loses to an earlier real session."""
    _, task = seed_project(tmp_path)
    real = seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)
    seed_session(
        tmp_path,
        task.id,
        provider_id=PROVIDER_CODEX,
        status=SessionStatus.FAILED,
        exit_reason=SessionExitReason.SPAWN_FAILED,
        exit_code=None,
    )

    with _store(tmp_path) as store:
        assert select_source_session(store, task).id == real.id


def test_only_spawn_failed_sessions_fall_back_to_newest(tmp_path: Path) -> None:
    """Verify selection still resolves when every recorded session failed to spawn."""
    _, task = seed_project(tmp_path)
    seed_session(
        tmp_path,
        task.id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.FAILED,
        exit_reason=SessionExitReason.SPAWN_FAILED,
        exit_code=None,
    )
    newest = seed_session(
        tmp_path,
        task.id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.FAILED,
        exit_reason=SessionExitReason.SPAWN_FAILED,
        exit_code=None,
    )

    with _store(tmp_path) as store:
        assert select_source_session(store, task).id == newest.id


def test_interrupted_session_is_a_valid_source(tmp_path: Path) -> None:
    """Verify an interrupted agent session still counts as real prior work."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)
    interrupted = seed_session(
        tmp_path,
        task.id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.INTERRUPTED,
        exit_reason=SessionExitReason.USER_INTERRUPTED,
        exit_code=130,
    )

    with _store(tmp_path) as store:
        assert select_source_session(store, task).id == interrupted.id


def test_explicit_session_override(tmp_path: Path) -> None:
    """Verify an explicitly requested source session wins over recency."""
    _, task = seed_project(tmp_path)
    older = seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CODEX)

    with _store(tmp_path) as store:
        assert select_source_session(store, task, explicit_session_id=older.id).id == older.id


def test_explicit_missing_session_raises(tmp_path: Path) -> None:
    """Verify an unknown explicit session identifier is rejected."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)

    with _store(tmp_path) as store, pytest.raises(SessionNotFoundError):
        select_source_session(store, task, explicit_session_id="sess_ghost")


def test_explicit_session_from_other_task_rejected(tmp_path: Path) -> None:
    """Verify an unrelated session can never be used as a handoff source."""
    project, task = seed_project(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    other_task = Task(project_id=project.id, title="Other", objective="Other objective")
    with SQLiteStateStore(db_file, auto_migrate=False) as store:
        store.save_task(other_task)
    foreign = seed_session(tmp_path, other_task.id)
    seed_session(tmp_path, task.id)

    with _store(tmp_path) as store, pytest.raises(SessionTaskMismatchError) as exc:
        select_source_session(store, task, explicit_session_id=foreign.id)

    assert task.id in str(exc.value)
