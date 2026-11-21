"""One-command Task creation and invocation-bound startup context."""

import importlib
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.run_service import RunService
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.application.workspace import open_workspace
from cortexshift.cli.app import app
from cortexshift.domain.handoff import MAX_HANDOFF_BYTES
from cortexshift.domain.mcp_binding import (
    ENV_INVOCATION_TOKEN,
    ENV_TASK_ID,
    INVOCATION_VERIFIER_KEY,
)
from cortexshift.domain.session import (
    STARTUP_OBJECTIVE_SERVED_KEY,
    SessionExitReason,
    SessionStatus,
)
from cortexshift.domain.task import TaskStatus
from tests.cli_runner import AnsiFreeCliRunner, unwrapped
from tests.integration.test_managed_mcp_grandchild import (
    _declared_mcp_env,
    _sanitized_env,
    _server_params,
)

OBJECTIVE = "    # Tâche 🧪\r\n\r\n```sh\r\n$(echo unsafe); & | < >\r\n```\r\n  "


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspace"
    ProjectInitializationService().initialize(root)
    monkeypatch.chdir(root)
    return root


def launch_runner(monkeypatch: pytest.MonkeyPatch, implementation: Mock) -> None:
    # Use the real application services while substituting provider discovery,
    # terminal approval and process execution. No installed provider can run.
    monkeypatch.setattr(RunService, "_check_tty", staticmethod(lambda: True))
    monkeypatch.setattr("cortexshift.application.run_service.shutil.which", lambda name: name)
    monkeypatch.setattr(
        "cortexshift.adapters.process_runner.SubprocessInteractiveProcessRunner.run_interactive",
        implementation,
    )


@pytest.mark.parametrize("source", ["relative", "absolute", "stdin", "inline"])
def test_objective_sources(project: Path, monkeypatch: pytest.MonkeyPatch, source: str) -> None:
    launch = Mock(return_value=0)
    launch_runner(monkeypatch, launch)

    @contextmanager
    def fake_terminal(*, piped: bool) -> Iterator[None]:
        assert piped == (source == "stdin")
        yield

    monkeypatch.setattr(
        importlib.import_module("cortexshift.cli.app"), "interactive_stdin", fake_terminal
    )
    input_bytes = None
    if source in {"relative", "absolute"}:
        subdir = project / "subdir"
        subdir.mkdir()
        monkeypatch.chdir(subdir)
        file = (
            subdir / "prompt with spaces.md"
            if source == "relative"
            else (project.parent / "prompt with spaces.md")
        )
        file.write_bytes(OBJECTIVE.encode())
        args = ["--file", file.name if source == "relative" else str(file)]
    elif source == "stdin":
        args = ["--stdin"]
        input_bytes = OBJECTIVE.encode()
    else:
        args = ["--objective", OBJECTIVE]

    result = AnsiFreeCliRunner().invoke(app, ["start", "claude", *args], input=input_bytes)
    assert result.exit_code == 0, result.output
    assert "retrieval was not observed" in unwrapped(result.output)
    workspace, store = open_workspace(project)
    with store:
        task_id = store.get_active_task_id(workspace.project.id)
        assert task_id is not None
        task = store.get_task(task_id)
        assert task is not None and task.objective == OBJECTIVE
        assert task.title == "Tâche 🧪"
        assert launch.call_args.kwargs["cwd"] == project
        assert launch.call_args.kwargs["env"][ENV_TASK_ID] == task.id
    assert OBJECTIVE not in result.output
    assert OBJECTIVE not in launch.call_args.kwargs["argv"]


# Pass only concise IDs to pytest. Windows also stores the current test name in the environment.
@pytest.mark.parametrize(
    ("args", "payload", "message"),
    [
        ([], None, "Choose exactly one"),
        (["--file", "missing.md"], None, "could not read"),
        (["--file", "."], None, "regular file"),
        (["--file", "invalid.md"], None, "UTF-8"),
        (["--file", "empty.md"], None, "objective is empty"),
        (["--file", "unreadable.md"], None, "could not read"),
        (["--stdin"], b"", "objective is empty"),
        (["--stdin"], b"\xff", "UTF-8"),
        (["--objective", "  "], None, "objective is empty"),
        (["--file", "missing.md", "--stdin"], None, "Choose exactly one"),
        (["--stdin", "--objective", "task"], None, "Choose exactly one"),
    ],
    ids=[
        "no-input",
        "missing",
        "directory",
        "bad-encoding",
        "blank-file",
        "unreadable",
        "blank-pipe",
        "bad-pipe",
        "blank-inline",
        "file-stdin",
        "stdin-inline",
    ],
)
def test_input_errors_leave_no_state(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    args: list[str],
    payload: bytes | None,
    message: str,
) -> None:
    (project / "invalid.md").write_bytes(b"\xff\xfe")
    (project / "empty.md").write_bytes(b" \n\t")
    (project / "unreadable.md").touch()
    read_bytes = Path.read_bytes

    def checked_read(path: Path) -> bytes:
        if path.name == "unreadable.md":
            raise PermissionError("fake permissions")
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", checked_read)
    launch = Mock(return_value=0)
    launch_runner(monkeypatch, launch)
    result = AnsiFreeCliRunner().invoke(app, ["start", "claude", *args], input=payload)
    assert result.exit_code != 0
    assert message.casefold() in unwrapped(result.output).casefold()
    launch.assert_not_called()
    workspace, store = open_workspace(project)
    with store:
        assert store.list_tasks(workspace.project.id) == []
        assert store.list_sessions() == []


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_markdown_through_real_mcp_stdio(
    project: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    objective = OBJECTIVE + ("  Full instructions 🧪 漢字 \\\r\n" * 4500) + "\n"
    assert len(objective.encode()) > MAX_HANDOFF_BYTES
    file = project / "prompt.md"
    file.write_bytes(objective.encode())
    declared: dict[str, str] = {}
    recovered: list[str] = []
    other_task_id: str | None = None

    async def request_context(argv: list[str]) -> None:
        nonlocal other_task_id
        # A fake provider follows the native startup instruction automatically.
        assert "get_task_objective" in argv[-1]
        assert "next_offset" in argv[-1]
        assert max(len(arg.encode()) for arg in argv) < 4096
        assert "Full instructions" not in " ".join(argv)
        declared.update(_declared_mcp_env(provider, argv))
        async with (
            stdio_client(_server_params(_sanitized_env(declared), project)) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            assert "get_task_objective" in {tool.name for tool in (await client.list_tools()).tools}
            # Changing the default pointer during a session must never retarget MCP.
            other = TaskWorkspaceService().start_task("Other Task", "Keep this objective separate")
            other_task_id = other.id
            skipped = await client.call_tool("get_task_objective", {"offset": 1})
            assert skipped.is_error
            offset: int | None = 0
            while offset is not None:
                response = await client.call_tool("get_task_objective", {"offset": offset})
                assert not response.is_error, response.content
                page = response.structured_content
                assert page is not None
                assert page["task_id"] == declared[ENV_TASK_ID]
                assert page["offset"] == offset
                assert len(page["text"]) <= 4000
                recovered.append(page["text"])
                offset = page["next_offset"]
            metadata = await client.call_tool("get_current_task", {"include_objective": False})
            assert not metadata.is_error
            assert metadata.structured_content is not None
            assert "objective" not in metadata.structured_content

    def fake_provider(*, argv: list[str], cwd: Path, env: dict[str, str]) -> int:
        assert cwd == project
        anyio.run(request_context, argv)
        assert declared[ENV_TASK_ID] == env[ENV_TASK_ID]
        return 0

    launch = Mock(side_effect=fake_provider)
    launch_runner(monkeypatch, launch)
    result = AnsiFreeCliRunner().invoke(app, ["start", provider, "--file", str(file)])
    assert result.exit_code == 0, result.output
    assert "MCP served the complete Task objective" in unwrapped(result.output)
    assert "".join(recovered) == objective
    assert declared[ENV_INVOCATION_TOKEN] not in result.output
    workspace, store = open_workspace(project)
    with store:
        tasks = store.list_tasks(workspace.project.id)
        assert {task.id for task in tasks} == {declared[ENV_TASK_ID], other_task_id}
        task = store.get_task(declared[ENV_TASK_ID])
        assert task is not None
        [session] = store.list_sessions()
        assert session.task_id == task.id
        assert store.get_active_task_id(workspace.project.id) == other_task_id
        assert task.objective == objective and task.status == TaskStatus.IN_PROGRESS
        assert session.status == SessionStatus.COMPLETED
        assert session.exit_reason == SessionExitReason.NORMAL_COMPLETION
        assert session.metadata[STARTUP_OBJECTIVE_SERVED_KEY] == len(objective)
        assert INVOCATION_VERIFIER_KEY not in session.metadata

    async def check_expired() -> None:
        async with (
            stdio_client(_server_params(_sanitized_env(declared), project)) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            assert "get_task_objective" not in {
                tool.name for tool in (await client.list_tools()).tools
            }

    anyio.run(check_expired)

    TaskWorkspaceService().activate_task(declared[ENV_TASK_ID])
    target = "codex" if provider == "claude" else "claude"
    preview = AnsiFreeCliRunner().invoke(app, ["handoff", "preview", target])
    assert preview.exit_code != 0
    assert "Essential handoff context" in unwrapped(preview.output)


def test_launch_failure_can_retry_same_task_and_rejects_implicit_replacement(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch = Mock(side_effect=[OSError("native spawn failed"), 0, 0])
    launch_runner(monkeypatch, launch)
    cli = AnsiFreeCliRunner()
    args = ["start", "claude", "--objective", "Initial secret objective"]
    failed = cli.invoke(app, args)
    assert failed.exit_code != 0
    assert "Initial secret objective" not in failed.output
    workspace, store = open_workspace(project)
    with store:
        [task] = store.list_tasks(workspace.project.id)
        [session] = store.list_sessions(task_id=task.id)
        assert store.get_active_task_id(workspace.project.id) == task.id
        assert session.exit_reason == SessionExitReason.SPAWN_FAILED
    retry = ["start", "claude", "--task", task.id]
    assert " ".join(["cortexshift", *retry]) in unwrapped(failed.output)

    repeated = cli.invoke(app, args)
    assert repeated.exit_code != 0
    assert "active Task already exists" in unwrapped(repeated.output)
    assert launch.call_count == 1

    resumed = cli.invoke(app, retry)
    assert resumed.exit_code == 0, resumed.output
    new = cli.invoke(app, [*args, "--replace-active"])
    assert new.exit_code == 0, new.output
    _, store = open_workspace(project)
    with store:
        assert len(store.list_tasks(workspace.project.id)) == 2
        preserved = store.get_task(task.id)
        assert preserved is not None and preserved.status == TaskStatus.IN_PROGRESS
        assert len(store.list_sessions(task_id=task.id)) == 2
        assert store.get_active_task_id(workspace.project.id) != task.id


def test_refuses_uninitialized_and_moved_workspace(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launch = Mock(return_value=0)
    launch_runner(monkeypatch, launch)
    cli = AnsiFreeCliRunner()
    args = ["start", "claude", "--objective", "Task"]
    monkeypatch.chdir(project.parent)
    result = cli.invoke(app, args)
    assert result.exit_code != 0
    assert "cortexshift init" in result.output
    displaced = project.parent / "copied"
    shutil.copytree(project, displaced)
    monkeypatch.chdir(displaced)
    database = displaced / ".cortexshift" / "state.sqlite3"
    before = database.read_bytes()
    result = cli.invoke(app, args)
    assert result.exit_code != 0
    assert "Workspace identity mismatch" in result.output
    assert database.read_bytes() == before
    launch.assert_not_called()


@pytest.mark.parametrize(
    ("provider", "missing", "message"),
    [("antigravity", False, "automatic Task context"), ("claude", True, "not found in PATH")],
)
def test_provider_preflight_has_no_task(
    project: Path, monkeypatch: pytest.MonkeyPatch, provider: str, missing: bool, message: str
) -> None:
    launch = Mock(return_value=0)
    launch_runner(monkeypatch, launch)
    if missing:
        monkeypatch.setattr("cortexshift.application.run_service.shutil.which", lambda _: None)
    result = AnsiFreeCliRunner().invoke(app, ["start", provider, "--objective", "Task"])
    assert result.exit_code != 0
    assert message in unwrapped(result.output)
    launch.assert_not_called()
    workspace, store = open_workspace(project)
    with store:
        assert store.list_tasks(workspace.project.id) == []
        assert store.list_sessions() == []


def test_start_after_completion_preserves_history_and_claude_can_exactly_resume(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = AnsiFreeCliRunner()
    assert (
        cli.invoke(app, ["task", "start", "--title", "Prior", "--objective", "Old"]).exit_code == 0
    )
    assert cli.invoke(app, ["task", "complete"]).exit_code == 0
    launch = Mock(return_value=0)
    launch_runner(monkeypatch, launch)

    started = cli.invoke(app, ["start", "claude", "--objective", "Continue work"])
    assert started.exit_code == 0, started.output
    resumed = cli.invoke(app, ["resume", "claude"])
    assert resumed.exit_code == 0, resumed.output
    assert launch.call_count == 2
    first, second = [call.kwargs for call in launch.call_args_list]
    assert "--session-id" in first["argv"] and "--resume" in second["argv"]
    assert first["env"][ENV_INVOCATION_TOKEN] != second["env"][ENV_INVOCATION_TOKEN]

    workspace, store = open_workspace(project)
    with store:
        assert len(store.list_tasks(workspace.project.id)) == 2
        [resume, original] = store.list_sessions()
        assert resume.resumed_from_session_id == original.id
        assert resume.task_id == original.task_id
        assert resume.native_session_id == original.native_session_id is not None


@pytest.mark.parametrize(
    ("method", "task_committed"),
    [("build_launch_spec", False), ("bind_managed_mcp", True)],
)
def test_bootstrap_preparation_failure_has_safe_lifecycle(
    project: Path, monkeypatch: pytest.MonkeyPatch, method: str, task_committed: bool
) -> None:
    failure = Mock(side_effect=OSError("simulated sensitive diagnostic"))
    monkeypatch.setattr(
        f"cortexshift.adapters.providers.claude.ClaudeRuntimeAdapter.{method}", failure
    )
    launch = Mock(return_value=0)
    launch_runner(monkeypatch, launch)

    result = AnsiFreeCliRunner().invoke(app, ["start", "claude", "--objective", "Task"])
    assert result.exit_code != 0
    assert "simulated sensitive diagnostic" not in result.output
    launch.assert_not_called()
    workspace, store = open_workspace(project)
    with store:
        tasks = store.list_tasks(workspace.project.id)
        sessions = store.list_sessions()
        if task_committed:
            assert len(tasks) == len(sessions) == 1
            assert sessions[0].exit_reason == SessionExitReason.SPAWN_FAILED
            assert f"--task {tasks[0].id}" in unwrapped(result.output)
        else:
            assert not tasks and not sessions


@pytest.mark.parametrize(
    ("exit_code", "status", "reason"),
    [
        (7, SessionStatus.FAILED, SessionExitReason.PROCESS_CRASHED),
        (130, SessionStatus.INTERRUPTED, SessionExitReason.USER_INTERRUPTED),
    ],
)
def test_native_exit_code_keeps_task_and_finalizes_session(
    project: Path,
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    status: SessionStatus,
    reason: SessionExitReason,
) -> None:
    launch_runner(monkeypatch, Mock(return_value=exit_code))
    result = AnsiFreeCliRunner().invoke(app, ["start", "claude", "--objective", "Task"])
    assert result.exit_code == exit_code
    assert f"Native session {status.value}" in unwrapped(result.output)
    workspace, store = open_workspace(project)
    with store:
        [session] = store.list_sessions()
        assert session.status == status and session.exit_reason == reason
        assert store.get_active_task_id(workspace.project.id) == session.task_id
