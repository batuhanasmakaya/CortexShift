"""Workspace identity checks through the application and CLI entry points."""

import os
import shutil
import sqlite3
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path, PureWindowsPath
from unittest.mock import Mock

import pytest

from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.adapters.sqlite.store import SQLiteStateStore, _database_uri
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.recovery_service import RecoveryService
from cortexshift.application.repository_service import RepositoryService
from cortexshift.application.resume_service import ResumeService
from cortexshift.application.run_service import RunService
from cortexshift.application.switch_service import ProviderHandoffRegistry, SwitchService
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.cli.app import app
from cortexshift.domain.errors import (
    McpContextError,
    ProjectConflictError,
    ProjectNotInitializedError,
)
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.mcp_binding import (
    ENV_PROJECT_ROOT,
    ENV_PROVIDER_ID,
    ENV_SESSION_ID,
    ENV_TASK_ID,
)
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.mcp.context import resolve_mcp_context
from cortexshift.mcp.facade import McpApplicationFacade
from cortexshift.tui.actions import TuiExitAction, TuiExitRequest
from cortexshift.tui.coordinator import TuiCoordinator
from cortexshift.tui.facade import TuiFacade
from tests.cli_runner import AnsiFreeCliRunner, unwrapped
from tests.factories import FakeCodexBootstrap, make_inspection, patch_which
from tests.unit.test_sqlite_v6_migrations import build_v5

MISMATCH = "Workspace identity mismatch"


def initialize(root: Path) -> Task:
    ProjectInitializationService().initialize(root)
    return TaskWorkspaceService().start_task(
        "Identity test", "Stay in this workspace", start_dir=root
    )


@pytest.mark.parametrize("operation", ["move", "copy"])
def test_managed_run_refuses_displaced_state(tmp_path: Path, operation: str) -> None:
    original = tmp_path / "original"
    displaced = tmp_path / "displaced"
    initialize(original)
    original_state = state_files(original)
    if operation == "move":
        original.rename(displaced)
    else:
        shutil.copytree(original, displaced)

    before = state_files(displaced)
    process = Mock(**{"run_interactive.return_value": 0})
    lease = Mock()
    service = RunService(
        process_runner=process,
        lease_manager=lease,
        which_fn=lambda name: name,
        is_tty_fn=lambda: True,
    )

    with pytest.raises(ProjectConflictError, match=MISMATCH) as raised:
        service.run("claude", start_dir=displaced)

    assert str(original.resolve()) in str(raised.value)
    assert str(displaced.resolve()) in str(raised.value)
    process.run_interactive.assert_not_called()
    lease.get_lease.assert_not_called()
    assert state_files(displaced) == before
    if operation == "move":
        assert not original.exists()
    else:
        assert state_files(original) == original_state


def test_cli_dry_run_refuses_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "original"
    displaced = tmp_path / "displaced"
    initialize(original)
    shutil.copytree(original, displaced)
    monkeypatch.chdir(displaced)
    patch_which(monkeypatch, "cortexshift.application.run_service", lambda name: name)

    result = AnsiFreeCliRunner().invoke(app, ["run", "claude", "--dry-run", "--json"])

    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert MISMATCH in unwrapped(result.stderr)


def state_files(root: Path) -> dict[str, bytes]:
    return {entry.name: entry.read_bytes() for entry in (root / ".cortexshift").iterdir()}


def save_session(
    root: Path, task: Task, provider: str = "claude", *, stale: bool = False
) -> Session:
    session = Session(
        task_id=task.id,
        provider_id=ProviderId(provider),
        native_session_id="28b17930-6b87-4a33-80a3-1d8fac3be497",
        status=SessionStatus.RUNNING if stale else SessionStatus.COMPLETED,
        ended_at=None if stale else utc_now(),
        exit_reason=None if stale else SessionExitReason.NORMAL_COMPLETION,
        exit_code=None if stale else 0,
    )
    with SQLiteStateStore(ProjectLocator.get_database_path(root)) as store:
        store.save_session(session)
    return session


def test_switch_refuses_before_git_bootstrap_session_or_handoff(tmp_path: Path) -> None:
    original = tmp_path / "original"
    copied = tmp_path / "copy"
    task = initialize(original)
    save_session(original, task)
    shutil.copytree(original, copied)
    before = state_files(original)
    bootstrap = FakeCodexBootstrap()
    process = Mock(**{"run_interactive.return_value": 0})
    inspector = Mock()
    service = SwitchService(
        registry=ProviderHandoffRegistry([CodexHandoffAdapter(headless_runner=bootstrap)]),
        process_runner=process,
        inspector=inspector,
        is_tty_fn=lambda: True,
        which_fn=lambda name: name,
    )

    with pytest.raises(ProjectConflictError, match=MISMATCH):
        service.switch("codex", start_dir=copied)
    assert not bootstrap.invocations
    inspector.inspect.assert_not_called()
    process.run_interactive.assert_not_called()
    assert state_files(original) == state_files(copied) == before


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_exact_resume_retains_source_session(tmp_path: Path, provider: str) -> None:
    original = tmp_path / "original"
    copied = tmp_path / "copy"
    task = initialize(original)
    source = save_session(original, task, provider)
    shutil.copytree(original, copied)
    before = state_files(original)
    process = Mock(**{"run_interactive.return_value": 0})
    service = ResumeService(
        process_runner=process, is_tty_fn=lambda: True, which_fn=lambda name: name
    )

    with pytest.raises(ProjectConflictError, match=MISMATCH):
        service.resume(provider, session_id=source.id, start_dir=copied)
    process.run_interactive.assert_not_called()
    assert state_files(original) == state_files(copied) == before
    with SQLiteStateStore(ProjectLocator.get_database_path(original)) as store:
        assert store.list_sessions(task_id=task.id) == [source]


@pytest.mark.parametrize("operation", ["checkpoint", "snapshot", "recovery", "recovery-preview"])
def test_displaced_state_cannot_inspect_git_or_mutate(tmp_path: Path, operation: str) -> None:
    original = tmp_path / "original"
    copied = tmp_path / "copy"
    task = initialize(original)
    save_session(original, task, stale=True)
    shutil.copytree(original, copied)
    before = state_files(original)
    inspector = Mock()
    lease = Mock()
    recovery = RecoveryService(inspector=inspector, lease_manager=lease)
    actions: dict[str, Callable[[], object]] = {
        "checkpoint": lambda: CheckpointService(inspector=inspector).create_checkpoint(
            start_dir=copied
        ),
        "snapshot": lambda: RepositoryService(inspector=inspector).capture_snapshot(copied),
        "recovery": lambda: recovery.recover(copied),
        "recovery-preview": lambda: recovery.recover(copied, dry_run=True),
    }

    with pytest.raises(ProjectConflictError, match=MISMATCH):
        actions[operation]()
    inspector.inspect.assert_not_called()
    lease.get_lease.assert_not_called()
    assert state_files(original) == state_files(copied) == before


@pytest.mark.parametrize("entry", ["init", "repo", "mcp"])
def test_identity_checked_before_migration(tmp_path: Path, entry: str) -> None:
    recorded = tmp_path / "original"
    moved = tmp_path / "moved"
    db_path = ProjectLocator.get_database_path(moved)
    db_path.parent.mkdir(parents=True)
    build_v5(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("UPDATE projects SET repo_path = ?", (str(recorded.resolve()),))
    before = state_files(moved)
    actions: dict[str, Callable[[], object]] = {
        "init": lambda: ProjectInitializationService().initialize(moved),
        "repo": lambda: RepositoryService().inspect_repository(moved),
        "mcp": lambda: resolve_mcp_context(project_root_override=moved, env_override={}),
    }

    with pytest.raises(McpContextError if entry == "mcp" else ProjectConflictError, match=MISMATCH):
        actions[entry]()
    assert state_files(moved) == before


def test_valid_legacy_state_still_migrates_on_init(tmp_path: Path) -> None:
    db_path = ProjectLocator.get_database_path(tmp_path)
    db_path.parent.mkdir()
    ids = build_v5(db_path)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("UPDATE projects SET repo_path = ?", (str(tmp_path.resolve()),))

    result = ProjectInitializationService().initialize(tmp_path)

    assert result.already_initialized
    assert result.project.id == ids["project"]
    with SQLiteStateStore(db_path, auto_migrate=False) as store:
        assert store.get_schema_version() == 6
        assert store.get_task(ids["task"]) is not None


def test_managed_mcp_checks_workspace_before_granting_write_access(tmp_path: Path) -> None:
    original = tmp_path / "original"
    copied = tmp_path / "copy"
    task = initialize(original)
    session = save_session(original, task, stale=True)
    shutil.copytree(original, copied)
    env = {
        ENV_PROJECT_ROOT: str(original),
        ENV_SESSION_ID: session.id,
        ENV_TASK_ID: task.id,
        ENV_PROVIDER_ID: str(session.provider_id),
    }

    context, store = resolve_mcp_context(env_override=env)
    with store:
        assert context.can_mutate()
        assert context.project_root == original.resolve()
        assert context.session_id == session.id
        assert context.task_id == task.id
        env[ENV_PROJECT_ROOT] = str(copied)
        before = state_files(copied)
        with pytest.raises(McpContextError, match=MISMATCH):
            resolve_mcp_context(env_override=env)
        assert state_files(copied) == before


def test_identity_probe_reads_committed_wal_frames(tmp_path: Path) -> None:
    initialize(tmp_path)
    db_path = ProjectLocator.get_database_path(tmp_path)
    with closing(sqlite3.connect(db_path)) as conn:
        with conn:
            conn.execute("UPDATE projects SET repo_path = ?", (str(tmp_path / "missing"),))
        wal = db_path.with_name(db_path.name + "-wal")
        assert wal.stat().st_size > 0
        before = db_path.read_bytes(), wal.read_bytes()

        with pytest.raises(ProjectConflictError, match=MISMATCH):
            RunService(which_fn=lambda name: name).dry_run("claude", start_dir=tmp_path)

        assert (db_path.read_bytes(), wal.read_bytes()) == before


@pytest.mark.parametrize("action", list(TuiExitAction))
def test_tui_explicit_project_owns_preview_and_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: TuiExitAction
) -> None:
    selected = tmp_path / "selected"
    unrelated = tmp_path / "unrelated"
    task = initialize(selected)
    initialize(unrelated)
    source = save_session(selected, task)
    monkeypatch.chdir(unrelated)
    process = Mock(**{"run_interactive.return_value": 0})
    bootstrap = FakeCodexBootstrap()
    inspector = Mock(side_effect=AssertionError("Unexpected direct inspector call"))
    inspector.inspect.side_effect = make_inspection
    switch = SwitchService(
        registry=ProviderHandoffRegistry([CodexHandoffAdapter(headless_runner=bootstrap)]),
        inspector=inspector,
        process_runner=process,
        is_tty_fn=lambda: True,
        which_fn=lambda name: name,
    )
    facade = TuiFacade.resolve(selected, switch_service=switch)
    before = state_files(unrelated)

    def dashboard() -> TuiExitRequest:
        assert facade.project_root == selected.resolve()
        assert facade.load_state().project.project_id == task.project_id
        assert facade.preview_switch("codex").source_session_id == source.id
        assert not bootstrap.invocations
        return TuiExitRequest(
            action,
            "codex" if action is TuiExitAction.SWITCH else "claude",
            selected_session_id=source.id if action is TuiExitAction.RESUME else None,
        )

    coordinator = TuiCoordinator(
        dashboard_runner=dashboard,
        switch_service=switch,
        run_service=RunService(
            process_runner=process, is_tty_fn=lambda: True, which_fn=lambda name: name
        ),
        resume_service=ResumeService(
            process_runner=process, is_tty_fn=lambda: True, which_fn=lambda name: name
        ),
    )

    result = coordinator.start(project_root=selected)

    assert result.session is not None and result.session.task_id == task.id
    assert result.session.resumed_from_session_id == (
        source.id if action is TuiExitAction.RESUME else None
    )
    process.run_interactive.assert_called_once()
    assert process.run_interactive.call_args.kwargs["cwd"] == selected.resolve()
    assert process.run_interactive.call_args.kwargs["env"][ENV_PROJECT_ROOT] == str(
        selected.resolve()
    )
    assert state_files(unrelated) == before


def test_tui_does_not_fall_back_to_parent_after_dashboard_exits(tmp_path: Path) -> None:
    initialize(tmp_path)
    nested = tmp_path / "nested"
    initialize(nested)
    facade = TuiFacade.resolve(nested)
    before = state_files(tmp_path)

    def dashboard() -> TuiExitRequest:
        (nested / ".cortexshift").rename(nested / "archived")
        return TuiExitRequest(TuiExitAction.RUN, "claude", workspace=facade.workspace)

    service = Mock()
    with pytest.raises(ProjectConflictError, match=MISMATCH):
        TuiCoordinator(dashboard_runner=dashboard, run_service=service).start(nested)
    service.run.assert_not_called()
    assert state_files(tmp_path) == before


def test_normal_non_git_project_resolves_relative_nested_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "test #100%"
    initialize(root)
    (root / "src" / "sub").mkdir(parents=True)
    monkeypatch.chdir(root / "src" / "sub")

    result = RunService(which_fn=lambda name: name).dry_run("claude", start_dir="../sub/..")

    assert result.cwd == root.resolve()
    assert ProjectInitializationService().initialize("../..").already_initialized


def test_workspace_symlink_alias_is_canonicalized(tmp_path: Path) -> None:
    root = tmp_path / "original"
    alias = tmp_path / "alias"
    initialize(root)
    try:
        alias.symlink_to(root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")
    with closing(sqlite3.connect(ProjectLocator.get_database_path(root))) as conn, conn:
        conn.execute("UPDATE projects SET repo_path = ?", (str(alias),))

    result = RunService(which_fn=lambda name: name).dry_run("claude", start_dir=alias)
    assert result.cwd == root.resolve()


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS /tmp alias")
def test_macos_tmp_alias_is_accepted() -> None:
    from tempfile import TemporaryDirectory

    with TemporaryDirectory(dir="/tmp") as temp:
        alias = Path(temp)
        initialize(alias)
        with closing(sqlite3.connect(ProjectLocator.get_database_path(alias))) as conn, conn:
            conn.execute("UPDATE projects SET repo_path = ?", (temp,))
        result = RunService(which_fn=lambda name: name).dry_run("claude", start_dir=alias)
        assert result.cwd == alias.resolve()


@pytest.mark.skipif(os.name != "nt", reason="Windows filesystem comparison")
def test_windows_mixed_case_and_drive_path(tmp_path: Path) -> None:
    root = tmp_path / "MixedCase Workspace"
    initialize(root)
    spelling = root.with_name(root.name.swapcase())
    if not spelling.exists():
        pytest.skip("This Windows directory uses case-sensitive lookup")
    with closing(sqlite3.connect(ProjectLocator.get_database_path(root))) as conn, conn:
        conn.execute("UPDATE projects SET repo_path = ?", (spelling.as_posix(),))

    result = RunService(which_fn=lambda name: name).dry_run("claude", start_dir=root)
    assert result.cwd.samefile(root)


def test_nonexistent_start_path_does_not_authorize_parent(tmp_path: Path) -> None:
    initialize(tmp_path)
    with pytest.raises(ProjectNotInitializedError):
        RunService(which_fn=lambda name: name).dry_run("claude", start_dir=tmp_path / "missing")


def test_redirected_state_refuses_before_opening_the_original(tmp_path: Path) -> None:
    original = tmp_path / "original"
    redirected = tmp_path / "redirected"
    initialize(original)
    redirected.mkdir()
    try:
        (redirected / ".cortexshift").symlink_to(
            original / ".cortexshift", target_is_directory=True
        )
    except OSError as exc:
        pytest.skip(f"Directory symlinks unavailable: {exc}")
    before = state_files(original)

    with pytest.raises(ProjectConflictError, match=MISMATCH):
        RunService(which_fn=lambda name: name).dry_run("claude", start_dir=redirected)
    assert state_files(original) == before


@pytest.mark.parametrize("command", [["serve"], ["status", "--json"]])
def test_mcp_cli_checks_explicit_binding_instead_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: list[str]
) -> None:
    original = tmp_path / "original"
    copied = tmp_path / "copy"
    task = initialize(original)
    session = save_session(original, task, stale=True)
    shutil.copytree(original, copied)
    monkeypatch.chdir(original)
    before = state_files(copied)
    env = {
        ENV_PROJECT_ROOT: str(copied),
        ENV_SESSION_ID: session.id,
        ENV_TASK_ID: task.id,
        ENV_PROVIDER_ID: str(session.provider_id),
    }

    result = AnsiFreeCliRunner().invoke(app, ["mcp", *command], env=env)

    assert result.exit_code == 1, result.output
    assert not result.stdout
    assert MISMATCH in unwrapped(result.stderr)
    assert state_files(copied) == before


def test_session_end_checkpoint_refuses_displaced_open_store(tmp_path: Path) -> None:
    original = tmp_path / "original"
    copied = tmp_path / "copy"
    task = initialize(original)
    session = save_session(original, task)
    shutil.copytree(original, copied)
    inspector = Mock()
    before = state_files(copied)

    with SQLiteStateStore(ProjectLocator.get_database_path(copied), auto_migrate=False) as store:
        assert (
            CheckpointService(inspector=inspector).capture_session_end_checkpoint(session, store)
            is None
        )

    inspector.inspect.assert_not_called()
    assert state_files(copied) == before


def test_tui_unbound_exit_request_cannot_use_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initialize(tmp_path)
    monkeypatch.chdir(tmp_path)
    service = Mock()
    coordinator = TuiCoordinator(
        dashboard_runner=lambda: TuiExitRequest(TuiExitAction.RUN, "claude"),
        run_service=service,
    )

    with pytest.raises(ProjectConflictError, match=MISMATCH):
        coordinator.start()
    service.run.assert_not_called()


def test_windows_sqlite_uris_keep_the_drive_or_unc_path() -> None:
    assert _database_uri(PureWindowsPath("C:/Work/100%/state.sqlite3")) == (
        "file:///C:/Work/100%25/state.sqlite3"
    )
    assert _database_uri(PureWindowsPath("//host/share/Work Folder/state.sqlite3")) == (
        "file:////host/share/Work%20Folder/state.sqlite3"
    )


@pytest.mark.skipif(os.name == "nt", reason="Windows may block moving an open SQLite database")
def test_run_revalidates_before_the_lease_if_workspace_moves_during_setup(tmp_path: Path) -> None:
    original = tmp_path / "original"
    moved = tmp_path / "moved"
    initialize(original)
    lease = Mock()
    process = Mock()

    def executable(name: str) -> str:
        original.rename(moved)
        return name

    service = RunService(
        process_runner=process,
        lease_manager=lease,
        which_fn=executable,
        is_tty_fn=lambda: True,
    )

    with pytest.raises(ProjectConflictError, match=MISMATCH):
        service.run("claude", start_dir=original)
    lease.get_lease.assert_not_called()
    process.run_interactive.assert_not_called()


def test_mcp_facade_refuses_a_store_from_another_workspace(tmp_path: Path) -> None:
    selected = tmp_path / "selected"
    unrelated = tmp_path / "unrelated"
    initialize(selected)
    initialize(unrelated)
    context, store = resolve_mcp_context(project_root_override=selected, env_override={})

    with (
        store,
        SQLiteStateStore(ProjectLocator.get_database_path(unrelated)) as unrelated_store,
        pytest.raises(ProjectConflictError, match=MISMATCH),
    ):
        McpApplicationFacade(context, unrelated_store)
