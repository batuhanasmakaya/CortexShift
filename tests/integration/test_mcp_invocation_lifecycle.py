"""Exercise MCP authority across independent SQLite connections and native resumes."""

from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.repository_service import RepositoryService
from cortexshift.application.resume_service import ResumeService
from cortexshift.application.run_service import RunService
from cortexshift.domain.errors import DatabaseStateError, McpReadOnlyError
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.mcp_binding import ENV_INVOCATION_TOKEN
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.mcp.context import McpExecutionContext, resolve_mcp_context
from cortexshift.mcp.facade import McpApplicationFacade
from tests.factories import make_inspection, seed_project
from tests.mcp_invocations import LiveMcpInvocations


@pytest.fixture
def invocation(
    tmp_path: Path, live_mcp: LiveMcpInvocations
) -> Iterator[tuple[McpExecutionContext, Session, Task, SQLiteStateStore, SQLiteStateStore]]:
    project, task = seed_project(tmp_path)
    session = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.RUNNING)
    db_path = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_path) as mcp_store, SQLiteStateStore(db_path) as launcher_store:
        token = live_mcp.open(tmp_path, launcher_store, session)
        context = McpExecutionContext(
            project_root=tmp_path,
            project_id=project.id,
            task_id=task.id,
            session_id=session.id,
            provider_id=session.provider_id,
            managed_session=True,
            invocation_token=token,
        )
        yield context, session, task, mcp_store, launcher_store


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("set_current_work", ("Progress",)),
        ("mark_completed", (["Progress"],)),
        ("add_remaining", (["Progress"],)),
        ("record_issue", ("Issue",)),
        ("record_decision", ("Decision",)),
        ("create_checkpoint", ()),
    ],
)
def test_reconciled_session_denies_all_six_writes(
    invocation: tuple[McpExecutionContext, Session, Task, SQLiteStateStore, SQLiteStateStore],
    method: str,
    args: tuple[Any, ...],
) -> None:
    context, session, task, mcp_store, launcher_store = invocation
    facade = McpApplicationFacade(context, mcp_store)
    live_row = launcher_store.get_session(session.id)
    assert live_row is not None
    # The launch lock is still held by the fixture. Reconciliation alone must deny writes.
    launcher_store.save_session(
        live_row.model_copy(
            update={"status": SessionStatus.INTERRUPTED, "reconciled_at": utc_now()}
        )
    )
    with pytest.raises(McpReadOnlyError, match="expired"):
        getattr(facade, method)(*args)
    assert launcher_store.get_task(task.id) == task
    assert launcher_store.list_checkpoints() == []


@pytest.mark.parametrize("operation", ["task", "checkpoint"])
def test_authorized_write_can_commit_before_finalization(
    invocation: tuple[McpExecutionContext, Session, Task, SQLiteStateStore, SQLiteStateStore],
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, session, task, mcp_store, launcher_store = invocation
    facade = McpApplicationFacade(context, mcp_store)
    authorized = Event()
    finalizing = Event()

    save_method = "save_task" if operation == "task" else "save_checkpoint"
    save = getattr(mcp_store, save_method)

    def pause_after_authorization(record: Any) -> None:
        authorized.set()
        assert finalizing.wait(5)
        save(record)

    def trace(statement: str) -> None:
        if "INSERT INTO sessions" in statement:
            finalizing.set()

    monkeypatch.setattr(mcp_store, save_method, pause_after_authorization)
    launcher_store._conn.set_trace_callback(trace)
    write: Callable[[], Any] = (
        (lambda: facade.set_current_work("Last report"))
        if operation == "task"
        else facade.create_checkpoint
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        report = pool.submit(write)
        assert authorized.wait(5)
        finalize = pool.submit(
            launcher_store.save_session,
            session.model_copy(update={"status": SessionStatus.COMPLETED, "ended_at": utc_now()}),
        )
        result = report.result(timeout=8)
        finalize.result(timeout=8)

    if operation == "task":
        updated = launcher_store.get_task(task.id)
        assert updated is not None and updated.current_work == "Last report"
    else:
        assert launcher_store.get_checkpoint(result.checkpoint_id) is not None
    with pytest.raises(McpReadOnlyError, match="expired"):
        write()


def test_finalization_that_owns_write_reservation_wins_task_race(
    invocation: tuple[McpExecutionContext, Session, Task, SQLiteStateStore, SQLiteStateStore],
) -> None:
    context, session, task, mcp_store, launcher_store = invocation
    facade = McpApplicationFacade(context, mcp_store)
    finalizing = Event()
    mcp_attempt = Event()

    def pause_finalization(statement: str) -> None:
        if statement == "COMMIT":
            finalizing.set()
            mcp_attempt.wait(5)

    def trace_mcp(statement: str) -> None:
        if statement.startswith("BEGIN IMMEDIATE"):
            mcp_attempt.set()

    launcher_store._conn.set_trace_callback(pause_finalization)
    mcp_store._conn.set_trace_callback(trace_mcp)
    with ThreadPoolExecutor(max_workers=2) as pool:
        finalize = pool.submit(
            launcher_store.save_session,
            session.model_copy(update={"status": SessionStatus.COMPLETED, "ended_at": utc_now()}),
        )
        assert finalizing.wait(5)
        write = pool.submit(facade.set_current_work, "Expired report")
        finalize.result(timeout=8)
        with pytest.raises(McpReadOnlyError, match="expired"):
            write.result(timeout=8)
    assert mcp_attempt.is_set()
    assert launcher_store.get_task(task.id) == task


@pytest.mark.parametrize("method", ["create_checkpoint", "record_decision"])
def test_finalization_can_win_while_checkpoint_observes_git(
    invocation: tuple[McpExecutionContext, Session, Task, SQLiteStateStore, SQLiteStateStore],
    method: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, session, _, mcp_store, launcher_store = invocation
    repo = RepositoryService()
    inspection_started = Event()
    allow_inspection = Event()

    def inspect(_: object) -> Any:
        inspection_started.set()
        assert allow_inspection.wait(5)
        return make_inspection(str(context.project_root), context.project_id)

    monkeypatch.setattr(repo, "inspect_repository", inspect)
    facade = McpApplicationFacade(context, mcp_store, repository_service=repo)
    args = () if method == "create_checkpoint" else ("Decision",)
    with ThreadPoolExecutor(max_workers=2) as pool:
        checkpoint = pool.submit(getattr(facade, method), *args)
        assert inspection_started.wait(5)
        try:
            finalized = pool.submit(
                launcher_store.save_session,
                session.model_copy(
                    update={"status": SessionStatus.COMPLETED, "ended_at": utc_now()}
                ),
            )
            finalized.result(timeout=3)
        finally:
            allow_inspection.set()
        with pytest.raises(McpReadOnlyError, match="expired"):
            checkpoint.result(timeout=8)
    assert launcher_store.list_checkpoints() == []
    assert launcher_store.list_snapshots(context.project_id) == []


def test_checkpoint_failure_rolls_back_its_snapshot(
    invocation: tuple[McpExecutionContext, Session, Task, SQLiteStateStore, SQLiteStateStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context, _, _, mcp_store, launcher_store = invocation
    repo = RepositoryService()
    inspection = make_inspection(str(context.project_root), context.project_id)
    monkeypatch.setattr(repo, "inspect_repository", lambda _: inspection)

    def fail_save(_: object) -> None:
        raise DatabaseStateError("Checkpoint write failed")

    monkeypatch.setattr(mcp_store, "save_checkpoint", fail_save)
    facade = McpApplicationFacade(context, mcp_store, repository_service=repo)
    with pytest.raises(DatabaseStateError, match="Checkpoint write failed"):
        facade.create_checkpoint()
    assert launcher_store.list_checkpoints() == []
    assert launcher_store.list_snapshots(context.project_id) == []


def test_native_resume_mints_new_authority_for_the_same_task(tmp_path: Path) -> None:
    _, task = seed_project(tmp_path)

    class Reporter:
        report: Callable[[dict[str, str]], None]

        def run_interactive(
            self, argv: list[str], cwd: Path | str, env: dict[str, str] | None = None
        ) -> int:
            assert env is not None
            self.report(env)
            return 0

    runner = Reporter()
    old_env: dict[str, str] = {}
    old_facade: McpApplicationFacade | None = None
    old_store: SQLiteStateStore | None = None

    def first_report(env: dict[str, str]) -> None:
        nonlocal old_facade, old_store
        old_env.update(env)
        context, old_store = resolve_mcp_context(env_override=env)
        old_facade = McpApplicationFacade(context, old_store)
        old_facade.set_current_work("Prior invocation")

    runner.report = first_report
    first = RunService(
        process_runner=runner, which_fn=lambda name: name, is_tty_fn=lambda: True
    ).run("claude", start_dir=tmp_path)
    assert old_facade is not None and old_store is not None

    def resumed_report(env: dict[str, str]) -> None:
        assert env[ENV_INVOCATION_TOKEN] != old_env[ENV_INVOCATION_TOKEN]
        assert old_facade is not None
        with pytest.raises(McpReadOnlyError, match="expired"):
            old_facade.record_issue("Stale client")
        # A prior invocation's token cannot be used even with the new invocation's IDs.
        forged = {**env, ENV_INVOCATION_TOKEN: old_env[ENV_INVOCATION_TOKEN]}
        old_context, replay = resolve_mcp_context(env_override=forged)
        with replay:
            assert old_context.read_only
        context, store = resolve_mcp_context(env_override=env)
        with store:
            assert context.task_id == task.id
            McpApplicationFacade(context, store).set_current_work("Resumed invocation")

    runner.report = resumed_report
    with old_store:
        resumed = ResumeService(
            process_runner=runner, which_fn=lambda name: name, is_tty_fn=lambda: True
        ).resume("claude", start_dir=tmp_path)
        assert isinstance(resumed, Session)
        assert resumed.id != first.id
        assert resumed.native_session_id == first.native_session_id
        assert resumed.resumed_from_session_id == first.id
        assert old_facade.get_current_task()["current_work"] == "Resumed invocation"
