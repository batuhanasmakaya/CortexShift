"""Regression tests for concurrent canonical Task writes."""

import multiprocessing
import os
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from multiprocessing.synchronize import Event as ProcessEvent
from pathlib import Path
from threading import Barrier, Event

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.task_service import TaskService
from cortexshift.cli.app import app
from cortexshift.domain.errors import (
    DatabaseStateError,
    NoActiveTaskError,
    TaskAlreadyCompletedError,
    TaskNotActivatableError,
    TaskTerminalError,
)
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task, TaskStatus
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade
from tests.factories import seed_project
from tests.mcp_invocations import LiveMcpInvocations


@pytest.fixture
def connections(
    tmp_path: Path,
) -> Iterator[tuple[Project, Task, SQLiteStateStore, SQLiteStateStore]]:
    project, task = seed_project(tmp_path, remaining=["Prior work"])
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"
    with (
        SQLiteStateStore(db_path, auto_migrate=False) as first,
        SQLiteStateStore(db_path, auto_migrate=False) as second,
    ):
        assert first.get_schema_version() == second.get_schema_version() == 6
        yield project, task, first, second


def pause_pointer_write(
    monkeypatch: pytest.MonkeyPatch, first: SQLiteStateStore, second: SQLiteStateStore
) -> Event:
    """Pause the first writer before its pointer write until the second requests its lock."""
    before_pointer = Event()
    attempting_write = Event()
    set_active_task_id = first.set_active_task_id

    def set_pointer(project_id: str, task_id: str | None) -> None:
        before_pointer.set()
        assert attempting_write.wait(timeout=10)
        set_active_task_id(project_id, task_id)

    def trace(statement: str) -> None:
        if statement.startswith("BEGIN IMMEDIATE"):
            attempting_write.set()

    monkeypatch.setattr(first, "set_active_task_id", set_pointer)
    second._conn.set_trace_callback(trace)
    return before_pointer


def test_concurrent_issue_and_remaining_update_preserve_both(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Start a competing update while the first writer is holding an in-memory Task."""
    issue_writer_loaded = Event()
    remaining_writer_ready = Event()
    issue_committed = Event()
    add_known_issues = Task.add_known_issues
    add_remaining_items = Task.add_remaining_items

    def pause_issue_writer(task: Task, items: list[str]) -> Task:
        issue_writer_loaded.set()
        assert remaining_writer_ready.wait(timeout=10)
        return add_known_issues(task, items)

    def pause_legacy_remaining_writer(task: Task, items: list[str]) -> Task:
        # The old path reaches here with a stale Task. Force its save to happen second.
        remaining_writer_ready.set()
        assert issue_committed.wait(timeout=10)
        return add_remaining_items(task, items)

    def signal_transaction_attempt(statement: str) -> None:
        # A serialized writer cannot load the Task yet. Let the first writer commit
        # when the second attempts to acquire its SQLite write transaction.
        if statement.startswith("BEGIN IMMEDIATE"):
            remaining_writer_ready.set()

    monkeypatch.setattr(Task, "add_known_issues", pause_issue_writer)
    monkeypatch.setattr(Task, "add_remaining_items", pause_legacy_remaining_writer)

    db_path = tmp_path / "state.sqlite3"
    with SQLiteStateStore(db_path) as first, SQLiteStateStore(db_path) as second:
        project = Project(name="Concurrent project", repo_path=str(tmp_path))
        first.save_project(project)
        first_tasks = TaskService(first)
        second_tasks = TaskService(second)
        task = first_tasks.start_task(project.id, "Concurrent task", "Save both updates")
        second._conn.set_trace_callback(signal_transaction_attempt)

        def record_issue() -> Task:
            try:
                return first_tasks.update_task(project.id, task.id, add_issues=["Bug"])
            finally:
                issue_committed.set()

        with ThreadPoolExecutor(max_workers=2) as executor:
            issue_write = executor.submit(record_issue)
            assert issue_writer_loaded.wait(timeout=10)
            remaining_write = executor.submit(
                second_tasks.update_task, project.id, task.id, add_remaining=["Fix bug"]
            )
            assert issue_write.result(timeout=10).known_issues == ["Bug"]
            assert remaining_write.result(timeout=10).remaining_items == ["Fix bug"]

        persisted = first_tasks.get_task(task.id)
        assert persisted.remaining_items == ["Fix bug"]
        assert persisted.known_issues == ["Bug"]


def test_concurrent_creation_and_activation(
    connections: tuple[Project, Task, SQLiteStateStore, SQLiteStateStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, original, first, second = connections
    first_tasks, second_tasks = TaskService(first), TaskService(second)
    before_pointer = pause_pointer_write(monkeypatch, first, second)

    with ThreadPoolExecutor(max_workers=2) as executor:
        create = executor.submit(first_tasks.start_task, project.id, "New task", "Start work")
        assert before_pointer.wait(timeout=10)
        assert second_tasks.list_tasks(project.id) == [original]
        assert second.get_active_task_id(project.id) == original.id

        activation = executor.submit(second_tasks.activate_task, project.id, original.id)
        created = create.result(timeout=10)
        assert activation.result(timeout=10) == original

    assert second_tasks.list_tasks(project.id) == [original, created]
    assert created.status is TaskStatus.IN_PROGRESS
    assert second.get_active_task_id(project.id) == original.id


@pytest.mark.parametrize(
    ("operation", "expected_error"),
    [
        ("activate-other", None),
        ("activate-completed", TaskNotActivatableError),
        ("complete-again", TaskAlreadyCompletedError),
        ("update-completed", TaskTerminalError),
    ],
)
def test_completion_serializes_competing_task_changes(
    connections: tuple[Project, Task, SQLiteStateStore, SQLiteStateStore],
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    expected_error: type[Exception] | None,
) -> None:
    project, original, first, second = connections
    first_tasks, second_tasks = TaskService(first), TaskService(second)
    other = first_tasks.start_task(project.id, "Other task", "Other work", set_active=False)
    before_pointer = pause_pointer_write(monkeypatch, first, second)

    def compete() -> Task:
        if operation == "activate-other":
            return second_tasks.activate_task(project.id, other.id)
        if operation == "activate-completed":
            return second_tasks.activate_task(project.id, original.id)
        if operation == "complete-again":
            return second_tasks.complete_task(project.id, original.id)
        return second_tasks.update_task(project.id, original.id, add_issues=["Late issue"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        complete = executor.submit(first_tasks.complete_task, project.id, original.id)
        assert before_pointer.wait(timeout=10)
        assert second_tasks.get_task(original.id) == original
        assert second.get_active_task_id(project.id) == original.id

        competing = executor.submit(compete)
        completed = complete.result(timeout=10)
        if expected_error is not None:
            with pytest.raises(expected_error):
                competing.result(timeout=10)
        else:
            assert competing.result(timeout=10) == other

    assert completed.status is TaskStatus.COMPLETED
    assert second_tasks.get_task(original.id) == completed
    assert second_tasks.get_task(other.id) == other
    expected_active = other.id if operation == "activate-other" else None
    assert second.get_active_task_id(project.id) == expected_active


@pytest.mark.parametrize("explicit_id", [False, True])
def test_activation_before_concurrent_completion(
    connections: tuple[Project, Task, SQLiteStateStore, SQLiteStateStore],
    monkeypatch: pytest.MonkeyPatch,
    explicit_id: bool,
) -> None:
    project, original, first, second = connections
    first_tasks, second_tasks = TaskService(first), TaskService(second)
    other = first_tasks.start_task(project.id, "Other task", "Other work", set_active=False)
    before_pointer = pause_pointer_write(monkeypatch, first, second)
    target_id = original.id if explicit_id else None

    with ThreadPoolExecutor(max_workers=2) as executor:
        activate = executor.submit(first_tasks.activate_task, project.id, other.id)
        assert before_pointer.wait(timeout=10)
        complete = executor.submit(second_tasks.complete_task, project.id, target_id)
        assert activate.result(timeout=10) == other
        completed = complete.result(timeout=10)

    expected = original if explicit_id else other
    untouched = other if explicit_id else original
    assert completed.id == expected.id
    assert completed.status is TaskStatus.COMPLETED
    assert second_tasks.get_task(expected.id) == completed
    assert second_tasks.get_task(untouched.id) == untouched
    assert second.get_active_task_id(project.id) == (other.id if explicit_id else None)


@pytest.mark.parametrize("operation", ["create", "complete"])
def test_pointer_failure_rolls_back_entire_lifecycle_change(
    connections: tuple[Project, Task, SQLiteStateStore, SQLiteStateStore], operation: str
) -> None:
    project, original, first, second = connections
    tasks = TaskService(first)
    first._conn.execute(
        """
        CREATE TRIGGER fail_active_write BEFORE UPDATE OF active_task_id ON project_runtime
        BEGIN
            SELECT RAISE(ABORT, 'injected pointer failure');
        END;
        """
    )

    with pytest.raises(DatabaseStateError, match="injected pointer failure"):
        if operation == "create":
            tasks.start_task(project.id, "Rolled back task", "Never persisted")
        else:
            tasks.complete_task(project.id)

    assert second.list_tasks(project.id) == [original]
    assert second.get_active_task_id(project.id) == original.id
    assert not first._conn.in_transaction
    first._conn.execute("DROP TRIGGER fail_active_write;")
    completed = tasks.complete_task(project.id)
    assert second.get_task(original.id) == completed
    assert second.get_active_task_id(project.id) is None


def test_busy_writer_returns_error_without_partial_writes(
    connections: tuple[Project, Task, SQLiteStateStore, SQLiteStateStore],
) -> None:
    project, original, first, second = connections
    tasks = TaskService(second)
    second._conn.execute("PRAGMA busy_timeout = 0;")

    with first.task_write_transaction():
        with pytest.raises(DatabaseStateError, match="write transaction. Try again"):
            tasks.complete_task(project.id)
        assert not second._conn.in_transaction
        assert tasks.get_active_task(project.id) == original

    assert tasks.complete_task(project.id).status is TaskStatus.COMPLETED


def _run_cli_contender(root: Path, task_id: str, attempting_write: ProcessEvent) -> None:
    configure = SQLiteStateStore._configure_connection

    def trace(statement: str) -> None:
        if statement.startswith("BEGIN IMMEDIATE"):
            attempting_write.set()

    def configure_with_trace(store: SQLiteStateStore) -> None:
        configure(store)
        store._conn.set_trace_callback(trace)

    os.chdir(root)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(SQLiteStateStore, "_configure_connection", configure_with_trace)
        app(["task", "update", task_id, "--add-issue", "Child process issue"])


def test_cli_process_sees_the_committed_task_before_updating(
    connections: tuple[Project, Task, SQLiteStateStore, SQLiteStateStore], tmp_path: Path
) -> None:
    _, original, first, second = connections
    context = multiprocessing.get_context("spawn")
    attempting_write = context.Event()
    child = context.Process(
        target=_run_cli_contender, args=(tmp_path, original.id, attempting_write)
    )
    try:
        with first.task_write_transaction():
            first.save_task(original.add_known_issues(["Parent process issue"]))
            child.start()
            assert attempting_write.wait(timeout=15)
            assert second.get_task(original.id) == original
        child.join(timeout=15)
        assert child.exitcode == 0
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=5)
        child.close()

    persisted = second.get_task(original.id)
    assert persisted is not None
    assert persisted.known_issues == ["Parent process issue", "Child process issue"]


def test_nested_write_scope_is_rejected_and_rolled_back(
    connections: tuple[Project, Task, SQLiteStateStore, SQLiteStateStore],
) -> None:
    project, original, first, second = connections
    with pytest.raises(DatabaseStateError, match="Nested"), first.task_write_transaction():
        first.save_task(original.with_current_work("Uncommitted"))
        TaskService(first).activate_task(project.id, original.id)
    assert second.get_task(original.id) == original


@pytest.mark.parametrize("share_connection", [False, True])
def test_concurrent_bound_mcp_writes_preserve_progress_and_decisions(
    connections: tuple[Project, Task, SQLiteStateStore, SQLiteStateStore],
    tmp_path: Path,
    share_connection: bool,
    live_mcp: LiveMcpInvocations,
) -> None:
    project, original, first, second = connections
    tasks = TaskService(first)
    session = Session(task_id=original.id, provider_id=PROVIDER_CODEX, status=SessionStatus.RUNNING)
    first.save_session(session)
    context = McpExecutionContext(
        project_root=tmp_path,
        project_id=project.id,
        task_id=original.id,
        session_id=session.id,
        provider_id=session.provider_id,
        managed_session=True,
    )
    context = live_mcp.authorize(context, first)
    live_session = first.get_session(session.id)
    mcp_first = McpApplicationFacade(context, first)
    mcp_second = McpApplicationFacade(context, first if share_connection else second)
    active = tasks.start_task(project.id, "Different task", "Must stay untouched")
    barrier = Barrier(2, timeout=10)
    issue_count = 12

    def report_progress() -> None:
        barrier.wait()
        mcp_first.set_current_work("Recording completed work")
        barrier.wait()
        mcp_first.mark_completed(["Prior work"])
        for index in range(issue_count):
            barrier.wait()
            mcp_first.record_issue(f"First {index}")

    def report_issues_and_decision() -> str:
        barrier.wait()
        mcp_second.record_issue("Found an issue")
        barrier.wait()
        decision = mcp_second.record_decision("Keep schema v6")
        for index in range(issue_count):
            barrier.wait()
            mcp_second.record_issue(f"Second {index}")
        return decision.checkpoint_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        progress = executor.submit(report_progress)
        reports = executor.submit(report_issues_and_decision)
        progress.result(timeout=30)
        checkpoint_id = reports.result(timeout=30)

    persisted = tasks.get_task(original.id)
    assert persisted.current_work == "Recording completed work"
    assert persisted.completed == ["Prior work"]
    assert persisted.remaining == []
    assert persisted.known_issues[0] == "Found an issue"
    for index in range(issue_count):
        start = 1 + 2 * index
        assert set(persisted.known_issues[start : start + 2]) == {
            f"First {index}",
            f"Second {index}",
        }
    assert len(persisted.known_issues) == 1 + 2 * issue_count
    checkpoint = second.get_checkpoint(checkpoint_id)
    assert checkpoint is not None
    assert checkpoint.task_id == original.id
    assert checkpoint.session_id == session.id
    assert checkpoint.payload.decisions == ["Keep schema v6"]
    assert tasks.get_active_task(project.id) == active

    completed = tasks.complete_task(project.id, original.id)
    with pytest.raises(TaskTerminalError, match="completed"):
        mcp_second.add_remaining(["Stale session report"])
    assert second.get_task(original.id) == completed
    assert second.get_session(session.id) == live_session
    assert tasks.get_active_task(project.id) == active


def test_active_update_does_not_fall_back_to_an_inactive_task(
    connections: tuple[Project, Task, SQLiteStateStore, SQLiteStateStore],
) -> None:
    project, original, first, second = connections
    completed = TaskService(first).complete_task(project.id, original.id)
    with pytest.raises(NoActiveTaskError):
        TaskService(second).update_task(project.id, add_issues=["No target"])
    assert second.get_task(original.id) == completed
