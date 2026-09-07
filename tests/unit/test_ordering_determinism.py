"""Two records can share one instant, and "newest" must still mean one of them.

`datetime.now(UTC)` is not a tie-breaker. Its resolution is coarse enough on some
platforms -- roughly 16 ms on Windows -- that records written in one burst genuinely
carry the same timestamp, and `ORDER BY <timestamp> DESC` alone then lets SQLite return
either one first. That is not a display detail: `list_sessions` decides which session a
handoff is built from, which native conversation `resume` reattaches to, and which
session a recovery checkpoint is bound to.

Every test here writes the same timestamp on purpose. None waits for the clock: sleeping
until the clock moves would hide the ambiguity rather than settle it, and would leave the
production behaviour untested on the machines where ties actually happen.
"""

from datetime import datetime
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.recovery_service import RecoveryService
from cortexshift.application.source_session import select_source_session
from cortexshift.domain.checkpoint import (
    CheckpointGitState,
    CheckpointKind,
    CheckpointPayload,
    CheckpointRecord,
    CheckpointTaskSnapshot,
)
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.handoff import HandoffRecord, HandoffStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task
from tests.factories import make_inspection, make_payload, seed_project, seed_session


@pytest.fixture
def instant() -> datetime:
    """One instant, reused by every record a test writes."""
    return utc_now()


def _store(root: Path) -> SQLiteStateStore:
    return SQLiteStateStore(root / ".cortexshift" / "state.sqlite3", auto_migrate=False)


def _handoff(project_id: str, task_id: str, session_id: str, created_at: datetime) -> HandoffRecord:
    return HandoffRecord(
        project_id=project_id,
        task_id=task_id,
        source_session_id=session_id,
        source_provider_id=PROVIDER_CLAUDE,
        target_provider_id=PROVIDER_CODEX,
        status=HandoffStatus.DELIVERED,
        created_at=created_at,
        payload=make_payload(),
    )


def _checkpoint(
    project_id: str, task_id: str, session_id: str, created_at: datetime
) -> CheckpointRecord:
    return CheckpointRecord(
        project_id=project_id,
        task_id=task_id,
        session_id=session_id,
        kind=CheckpointKind.MANUAL,
        created_at=created_at,
        payload=CheckpointPayload(
            task=CheckpointTaskSnapshot(
                task_id=task_id,
                task_title="OAuth support",
                task_status="in_progress",
                objective="Add OAuth2 PKCE support.",
            ),
            git_state=CheckpointGitState(
                status=RepositoryInspectionStatus.READY,
                available=True,
                note="Git inspection ready",
                branch="main",
                dirty=False,
            ),
        ),
    )


# --- handoffs -------------------------------------------------------------


def test_handoffs_with_one_timestamp_list_later_persisted_first(
    tmp_path: Path, instant: datetime
) -> None:
    """Newest-first listing is total, not partial."""
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)

    with _store(tmp_path) as store:
        earlier = _handoff(project.id, task.id, session.id, instant)
        later = _handoff(project.id, task.id, session.id, instant)
        store.save_handoff(earlier)
        store.save_handoff(later)

        assert earlier.created_at == later.created_at
        listed = store.list_handoffs(project_id=project.id)
        assert [record.id for record in listed] == [later.id, earlier.id]


def test_latest_handoff_with_one_timestamp_is_the_later_persisted_one(
    tmp_path: Path, instant: datetime
) -> None:
    """`limit=1` is how every "latest handoff" lookup is expressed."""
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)

    with _store(tmp_path) as store:
        earlier = _handoff(project.id, task.id, session.id, instant)
        later = _handoff(project.id, task.id, session.id, instant)
        store.save_handoff(earlier)
        store.save_handoff(later)

        assert [r.id for r in store.list_handoffs(project_id=project.id, limit=1)] == [later.id]


# --- checkpoints ----------------------------------------------------------


def test_latest_checkpoint_with_one_timestamp_is_the_later_persisted_one(
    tmp_path: Path, instant: datetime
) -> None:
    """`get_latest_checkpoint` feeds handoff enrichment and the switch preview."""
    project, task = seed_project(tmp_path)
    session = seed_session(tmp_path, task.id)

    with _store(tmp_path) as store:
        earlier = _checkpoint(project.id, task.id, session.id, instant)
        later = _checkpoint(project.id, task.id, session.id, instant)
        store.save_checkpoint(earlier)
        store.save_checkpoint(later)

        newest = store.get_latest_checkpoint(task.id)
        assert newest is not None
        assert newest.id == later.id
        assert [c.id for c in store.list_checkpoints(task_id=task.id)] == [later.id, earlier.id]


# --- sessions -------------------------------------------------------------


def test_sessions_with_one_started_at_list_later_persisted_first(
    tmp_path: Path, instant: datetime
) -> None:
    """The ordering every "latest session" caller depends on."""
    project, task = seed_project(tmp_path)

    with _store(tmp_path) as store:
        earlier = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE, started_at=instant)
        later = Session(task_id=task.id, provider_id=PROVIDER_CODEX, started_at=instant)
        store.save_session(earlier)
        store.save_session(later)

        assert [s.id for s in store.list_sessions(task_id=task.id)] == [later.id, earlier.id]


def test_source_session_with_one_started_at_is_the_later_persisted_one(
    tmp_path: Path, instant: datetime
) -> None:
    """Which session a handoff is built from must not be a coin toss."""
    project, task = seed_project(tmp_path)

    with _store(tmp_path) as store:
        earlier = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE, started_at=instant)
        later = Session(task_id=task.id, provider_id=PROVIDER_CODEX, started_at=instant)
        store.save_session(earlier)
        store.save_session(later)

        stored_task = store.get_task(task.id)
        assert stored_task is not None
        assert select_source_session(store, stored_task).id == later.id


class _StaticInspector:
    """A repository inspection that reports a dirty tree without running Git."""

    def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
        return make_inspection(project_root=project_root, project_id=project_id)


def test_recovery_binds_its_checkpoint_to_the_later_persisted_stale_session(
    tmp_path: Path, instant: datetime
) -> None:
    """The recovery checkpoint records which session it reconciled around.

    That identifier is durable and protocol-visible, so picking the wrong one of two
    equally-timestamped stale sessions is a persisted mistake, not a cosmetic one.
    """
    project, task = seed_project(tmp_path)

    with _store(tmp_path) as store:
        earlier = Session(
            task_id=task.id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.INITIALIZING,
            started_at=instant,
        )
        later = Session(
            task_id=task.id,
            provider_id=PROVIDER_CODEX,
            status=SessionStatus.RUNNING,
            started_at=instant,
        )
        store.save_session(earlier)
        store.save_session(later)

    report = RecoveryService(inspector=_StaticInspector()).recover(start_dir=tmp_path)

    assert set(report.stale_session_ids) == {earlier.id, later.id}
    assert report.checkpoint_id is not None
    with _store(tmp_path) as store:
        checkpoint = store.get_checkpoint(report.checkpoint_id)
        assert checkpoint is not None
        assert checkpoint.session_id == later.id


# --- snapshots and tasks --------------------------------------------------


def test_snapshots_with_one_captured_at_list_later_persisted_first(
    tmp_path: Path, instant: datetime
) -> None:
    """`repo snapshots` promises newest-first; ties must not reshuffle it."""
    project, _task = seed_project(tmp_path)

    with _store(tmp_path) as store:
        earlier = make_inspection(project_id=project.id).snapshot
        later = make_inspection(project_id=project.id).snapshot
        assert earlier is not None and later is not None
        earlier = earlier.model_copy(update={"captured_at": instant})
        later = later.model_copy(update={"captured_at": instant})
        store.save_snapshot(earlier)
        store.save_snapshot(later)

        listed = store.list_snapshots(project_id=project.id)
        assert [s.id for s in listed] == [later.id, earlier.id]


def test_tasks_with_one_created_at_list_in_the_order_they_were_persisted(
    tmp_path: Path, instant: datetime
) -> None:
    """The task list is oldest-first, which is the same rule read the other way."""
    project, first = seed_project(tmp_path)

    with _store(tmp_path) as store:
        second = Task(project_id=project.id, title="Second", objective="Second", created_at=instant)
        third = Task(project_id=project.id, title="Third", objective="Third", created_at=instant)
        store.save_task(second)
        store.save_task(third)

        listed = [t.id for t in store.list_tasks(project.id)]
        assert listed.index(second.id) < listed.index(third.id)


# --- the rule itself ------------------------------------------------------


def test_a_newer_timestamp_still_outranks_insertion_order(tmp_path: Path) -> None:
    """The tie-break breaks ties only; it never overrides the timestamp.

    Guards against the ordering silently degrading into "last written wins".
    """
    project, task = seed_project(tmp_path)
    older = utc_now().replace(year=2020)
    newer = utc_now()

    with _store(tmp_path) as store:
        # Persist the newer record first, so insertion order disagrees with chronology.
        recent = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE, started_at=newer)
        ancient = Session(task_id=task.id, provider_id=PROVIDER_ANTIGRAVITY, started_at=older)
        store.save_session(recent)
        store.save_session(ancient)

        assert [s.id for s in store.list_sessions(task_id=task.id)] == [recent.id, ancient.id]
