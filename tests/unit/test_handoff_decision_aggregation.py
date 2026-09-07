"""Unit tests for task-scoped decision aggregation across checkpoint history.

A checkpoint is an immutable point-in-time observation, so `record_decision` mints its own
checkpoint and later checkpoints never copy that decision forward. Task-level durability is
reconstructed at handoff time instead. These tests pin the aggregation semantics, the
truthfulness of `decisions_known`, and the deterministic ordering of the backing query.
"""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.handoff_builder import HandoffBuilder, aggregate_task_decisions
from cortexshift.domain.checkpoint import (
    CheckpointGitState,
    CheckpointKind,
    CheckpointPayload,
    CheckpointRecord,
    CheckpointTaskSnapshot,
)
from cortexshift.domain.errors import DatabaseStateError
from cortexshift.domain.git import RepositoryInspectionStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task
from tests.factories import make_inspection

BASE_TIME = datetime.fromisoformat("2026-09-07T12:00:00+00:00")


def _checkpoint(
    task_id: str,
    decisions: list[str],
    project_id: str = "proj_agg",
    kind: CheckpointKind = CheckpointKind.MANUAL,
    created_at: datetime | None = None,
) -> CheckpointRecord:
    """Build a persisted-shape checkpoint record without touching Git or the builder."""
    return CheckpointRecord(
        project_id=project_id,
        task_id=task_id,
        kind=kind,
        created_at=created_at if created_at is not None else utc_now(),
        payload=CheckpointPayload(
            task=CheckpointTaskSnapshot(
                task_id=task_id,
                task_title="T",
                task_status="in_progress",
                objective="O",
            ),
            git_state=CheckpointGitState(
                status=RepositoryInspectionStatus.READY,
                available=True,
                note="observed",
            ),
            decisions=decisions,
        ),
    )


# --- Pure aggregation semantics ---


def test_decisions_aggregate_in_chronological_first_seen_order() -> None:
    """Case C: every decision across the task's history appears, oldest first."""
    history = [
        _checkpoint("task_1", ["Adopt SQLite WAL mode"]),
        _checkpoint("task_1", []),
        _checkpoint("task_1", ["Enforce pure stdout wire protocol", "Bound decision length"]),
        _checkpoint("task_1", [], kind=CheckpointKind.SESSION_END),
    ]

    assert aggregate_task_decisions(history, "task_1") == [
        "Adopt SQLite WAL mode",
        "Enforce pure stdout wire protocol",
        "Bound decision length",
    ]


def test_exact_duplicate_decisions_are_emitted_once_at_first_occurrence() -> None:
    """Case D: a redundantly re-recorded decision keeps only its first occurrence."""
    history = [
        _checkpoint("task_1", ["Adopt SQLite WAL mode"]),
        _checkpoint("task_1", ["Use stdio transport"]),
        _checkpoint("task_1", ["Adopt SQLite WAL mode", "Zero telemetry"]),
        _checkpoint("task_1", ["Adopt SQLite WAL mode"]),
    ]

    assert aggregate_task_decisions(history, "task_1") == [
        "Adopt SQLite WAL mode",
        "Use stdio transport",
        "Zero telemetry",
    ]


def test_deduplication_is_exact_equality_and_never_normalises_text() -> None:
    """Near-duplicates stay distinct: no trimming, case folding, or fuzzy matching."""
    history = [
        _checkpoint(
            "task_1",
            [
                "Adopt SQLite WAL mode",
                "Adopt SQLite WAL mode.",
                "adopt sqlite wal mode",
                "Adopt  SQLite WAL mode",
            ],
        ),
        _checkpoint("task_1", [" Adopt SQLite WAL mode "]),
    ]

    assert aggregate_task_decisions(history, "task_1") == [
        "Adopt SQLite WAL mode",
        "Adopt SQLite WAL mode.",
        "adopt sqlite wal mode",
        "Adopt  SQLite WAL mode",
        " Adopt SQLite WAL mode ",
    ]


def test_aggregation_drops_records_belonging_to_another_task() -> None:
    """Case F: aggregation is scoped to one task even if handed foreign records."""
    history = [
        _checkpoint("task_1", ["Task one decision"]),
        _checkpoint("task_2", ["Task two decision"]),
        _checkpoint("task_1", ["Task one second decision"]),
    ]

    assert aggregate_task_decisions(history, "task_1") == [
        "Task one decision",
        "Task one second decision",
    ]
    assert aggregate_task_decisions(history, "task_2") == ["Task two decision"]


def test_aggregation_of_empty_history_is_empty() -> None:
    """Case G: a task with no recorded decision aggregates to nothing."""
    assert aggregate_task_decisions([], "task_1") == []
    assert aggregate_task_decisions([_checkpoint("task_1", [])], "task_1") == []


def test_aggregation_ignores_blank_decision_entries() -> None:
    """Whitespace-only residue in stored payloads is not a decision."""
    history = [_checkpoint("task_1", ["   ", "", "Real decision"])]

    assert aggregate_task_decisions(history, "task_1") == ["Real decision"]


def test_aggregation_does_not_mutate_the_checkpoints_it_reads() -> None:
    """Aggregation is pure: historical checkpoints are never rewritten."""
    record = _checkpoint("task_1", ["Adopt SQLite WAL mode"])
    before = record.model_dump_json()

    aggregate_task_decisions([record, record], "task_1")

    assert record.model_dump_json() == before


# --- Builder truthfulness ---


def _build(task_decisions: list[str] | None, latest: CheckpointRecord | None = None):
    project = Project(name="Proj", repo_path="/repo")
    task = Task(project_id=project.id, title="T", objective="O")
    session = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.COMPLETED)
    return HandoffBuilder().build(
        project=project,
        task=task,
        source_session=session,
        inspection=make_inspection(),
        target_provider_id=PROVIDER_CODEX,
        latest_checkpoint=latest,
        task_decisions=task_decisions,
    )


def test_newest_empty_checkpoint_does_not_erase_older_task_decisions() -> None:
    """Case E: a decision-less newest checkpoint must not force decisions_known false."""
    newest_without_decisions = _checkpoint("task_1", [], kind=CheckpointKind.SESSION_END)

    payload = _build(["Adopt SQLite WAL mode"], latest=newest_without_decisions)

    assert payload.decisions_known is True
    assert payload.important_decisions == ["Adopt SQLite WAL mode"]


def test_empty_aggregate_reports_decisions_unknown() -> None:
    """Case G: absence is still reported honestly when the task truly has none."""
    payload = _build([])

    assert payload.decisions_known is False
    assert payload.important_decisions == []


def test_supplied_aggregate_outranks_the_latest_checkpoint_payload() -> None:
    """The task-scoped aggregate is authoritative for the decisions section."""
    latest = _checkpoint("task_1", ["Only in the newest checkpoint"])

    payload = _build(["Aggregated first", "Aggregated second"], latest=latest)

    assert payload.important_decisions == ["Aggregated first", "Aggregated second"]


# --- Deterministic backing query ---


def _seeded_store(
    tmp_path: Path, task_count: int = 1
) -> tuple[SQLiteStateStore, Project, list[Task]]:
    """Open a real store holding one project and `task_count` persisted tasks."""
    store = SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3")
    project = Project(name="P", repo_path=str(tmp_path))
    store.save_project(project)
    tasks = []
    for index in range(task_count):
        task = Task(project_id=project.id, title=f"Task {index}", objective="O")
        store.save_task(task)
        tasks.append(task)
    return store, project, tasks


def test_task_checkpoint_history_is_chronological_and_task_scoped(tmp_path: Path) -> None:
    """History reads oldest first and never spans tasks."""
    store, project, (task_a, task_b) = _seeded_store(tmp_path, task_count=2)

    older = _checkpoint(task_a.id, ["First"], project_id=project.id, created_at=BASE_TIME)
    newer = _checkpoint(
        task_a.id, ["Second"], project_id=project.id, created_at=BASE_TIME + timedelta(minutes=5)
    )
    foreign = _checkpoint(
        task_b.id, ["Foreign"], project_id=project.id, created_at=BASE_TIME + timedelta(minutes=1)
    )
    # Saved newest-first so insertion order cannot be mistaken for chronological order.
    for record in (newer, foreign, older):
        store.save_checkpoint(record)

    history = store.list_task_checkpoint_history(task_a.id)

    assert [c.id for c in history] == [older.id, newer.id]
    assert aggregate_task_decisions(history, task_a.id) == ["First", "Second"]
    assert [c.id for c in store.list_task_checkpoint_history(task_b.id)] == [foreign.id]
    store.close()


def test_identical_timestamps_fall_back_to_rowid_order(tmp_path: Path) -> None:
    """Coarse clock granularity must not make aggregation order nondeterministic."""
    store, project, (task,) = _seeded_store(tmp_path)

    first = _checkpoint(task.id, ["First"], project_id=project.id, created_at=BASE_TIME)
    second = _checkpoint(task.id, ["Second"], project_id=project.id, created_at=BASE_TIME)
    third = _checkpoint(task.id, ["Third"], project_id=project.id, created_at=BASE_TIME)
    for record in (first, second, third):
        store.save_checkpoint(record)

    for _ in range(5):
        history = store.list_task_checkpoint_history(task.id)
        assert [c.id for c in history] == [first.id, second.id, third.id]
        assert aggregate_task_decisions(history, task.id) == ["First", "Second", "Third"]

    store.close()


def test_task_checkpoint_history_is_unbounded(tmp_path: Path) -> None:
    """Aggregation must see the whole history, not the default listing page."""
    store, project, (task,) = _seeded_store(tmp_path)

    total = 25
    for index in range(total):
        store.save_checkpoint(
            _checkpoint(
                task.id,
                [f"Decision {index:02d}"],
                project_id=project.id,
                created_at=BASE_TIME + timedelta(minutes=index),
            )
        )

    history = store.list_task_checkpoint_history(task.id)

    assert len(history) == total
    assert aggregate_task_decisions(history, task.id) == [
        f"Decision {index:02d}" for index in range(total)
    ]
    store.close()


def test_foreign_task_id_cannot_be_persisted(tmp_path: Path) -> None:
    """Task isolation is also enforced by the schema, not only by the aggregation filter."""
    store, project, (task,) = _seeded_store(tmp_path)

    with pytest.raises(DatabaseStateError):
        store.save_checkpoint(_checkpoint("task_does_not_exist", ["X"], project_id=project.id))

    assert store.list_task_checkpoint_history(task.id) == []
    store.close()
