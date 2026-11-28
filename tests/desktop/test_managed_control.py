"""Desktop IPC, terminal tickets and canonical provider workflow with fake providers."""

import json
import os
from io import BytesIO
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from cortexshift.adapters.advisory_lock import release_lock, try_lock
from cortexshift.adapters.workspace_lease import FileWorkspaceLease
from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.run_service import RunService
from cortexshift.application.switch_service import SwitchService
from cortexshift.application.workspace import open_workspace
from cortexshift.desktop import terminal
from cortexshift.desktop.bridge import Bridge, serve
from cortexshift.domain.handoff import MAX_HANDOFF_BYTES
from cortexshift.domain.mcp_binding import ENV_INVOCATION_TOKEN, ENV_SESSION_ID
from cortexshift.domain.provider import PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionStatus
from tests.desktop.test_bridge import request
from tests.factories import FakeCodexBootstrap
from tests.integration.test_managed_mcp_grandchild import (
    _declared_mcp_env,
    _sanitized_env,
    _server_params,
)

OBJECTIVE = "  # March 🧪\r\n" + "  Preserve whitespace and Unicode 文\r\n" * 1900
TASK_ID = "task_" + "1" * 32


def test_update_guard_checks_detached_and_removed_project_jobs(tmp_path: Path) -> None:
    jobs = tmp_path / terminal.project_key("verified")
    job = jobs / ("job-" + "b" * 32)
    job.mkdir(parents=True)
    (jobs / "current").write_text(job.name, encoding="ascii")
    terminal._state(job, "pending")
    (job / "request.json").touch()
    assert terminal.unresolved_jobs(tmp_path, {"verified"})
    (job / "request.json").unlink()
    terminal._state(job, "running")
    fd = try_lock(job / "worker.lock", create=True)
    assert fd is not None
    try:
        assert terminal.unresolved_jobs(tmp_path, {"verified"})
    finally:
        release_lock(fd)
    assert terminal.unresolved_jobs(tmp_path, set())
    assert not terminal.unresolved_jobs(tmp_path, {"verified"})
    terminal._state(job, "completed")
    assert not terminal.unresolved_jobs(tmp_path, set())


def test_update_guard_rejects_corrupt_action_pointer(tmp_path: Path) -> None:
    jobs = tmp_path / ("a" * 64)
    jobs.mkdir()
    (jobs / "current").write_text("../other", encoding="ascii")
    with pytest.raises(ValueError):
        terminal.unresolved_jobs(tmp_path, set())


class Desktop:
    def __init__(self, root: Path, spool: Path) -> None:
        project = ProjectInitializationService().initialize(root)
        self.binding = {"root": str(root.resolve()), "project_id": project.project.id}
        self.bridge = Bridge()
        self.spool = {"control_dir": str(spool.resolve())}

    def call(self, op: str, **fields: Any) -> dict[str, Any]:
        return request(self.bridge, op, **self.binding, **fields)

    def create(self, *, activate: bool = True) -> dict[str, Any]:
        data = OBJECTIVE.encode()
        request_data = {
            "op": "create_task",
            "id": 1,
            "version": 1,
            "title": "Desktop continuity",
            "task_id": TASK_ID,
            "activate": activate,
            "expected_active": None,
            "objective_bytes": len(data),
            **self.binding,
        }
        response = BytesIO()
        serve(BytesIO(json.dumps(request_data).encode() + b"\n" + data), response)
        return dict(json.loads(response.getvalue()))

    @staticmethod
    def action(kind: str, provider: str | None = None, **option: Any) -> dict[str, Any]:
        return {"kind": kind, "provider": provider, "task_id": TASK_ID, **option}

    def submit(self, action: dict[str, Any], plan: dict[str, Any], index: int) -> dict[str, Any]:
        return self.call(
            "submit", **self.spool, action=action, plan=plan, operation_id=f"{index:032x}"
        )

    def status(self) -> dict[str, Any]:
        return dict(self.call("action_status", **self.spool)["result"]["action"])


@pytest.fixture
def desktop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Desktop:
    work = tmp_path / "work"
    monkeypatch.setenv("PATH", os.environ["PATH"])
    monkeypatch.setattr(terminal, "terminal_available", lambda: True)
    monkeypatch.setattr(RunService, "_check_tty", staticmethod(lambda: True))
    monkeypatch.setattr(SwitchService, "_check_tty", staticmethod(lambda: True))
    monkeypatch.setattr(
        "cortexshift.application.desktop_control.provider_lookup",
        lambda *_: lambda name: str(tmp_path / "executables" / name),
    )
    return Desktop(work, tmp_path / "private jobs")


def test_desktop_managed_workflow(
    desktop: Desktop,
    monkeypatch: pytest.MonkeyPatch,
    codex_bootstrap: FakeCodexBootstrap,
) -> None:
    assert desktop.create()["result"] == {"id": TASK_ID, "active": True}
    assert len(OBJECTIVE.encode()) > MAX_HANDOFF_BYTES
    jobs: list[Path] = []
    bindings: list[dict[str, str]] = []
    monkeypatch.setattr(terminal, "_open_terminal", jobs.append)
    checkpoint_id: str | None = None

    async def use_context(binding: dict[str, str], phase: int, cwd: Path) -> None:
        nonlocal checkpoint_id
        async with (
            stdio_client(_server_params(_sanitized_env(binding), cwd)) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            pages: list[str] = []
            offset: int | None = 0
            while offset is not None:
                result = await client.call_tool("get_task_objective", {"offset": offset})
                assert not result.is_error and result.structured_content is not None
                pages.append(result.structured_content["text"])
                offset = result.structured_content["next_offset"]
            assert "".join(pages) == OBJECTIVE
            if phase == 0:
                progress = await client.call_tool("mark_completed", {"items": ["Parser built"]})
                assert not progress.is_error
                checkpoint = await client.call_tool(
                    "create_checkpoint",
                    {"decisions": ["Keep the parser API"], "test_summary": "Two failures reported"},
                )
                assert not checkpoint.is_error and checkpoint.structured_content is not None
                checkpoint_id = checkpoint.structured_content["checkpoint_id"]
            elif phase == 1:
                assert "Keep the parser API" in codex_bootstrap.input_texts[-1]
                assert "Two failures reported" in codex_bootstrap.input_texts[-1]
                history = await client.call_tool("get_task_history")
                assert not history.is_error and history.structured_content is not None
                assert history.structured_content["important_decisions"] == ["Keep the parser API"]
            elif phase == 3:
                handoff = await client.call_tool("get_handoff_context")
                assert not handoff.is_error
                text = " ".join(item.text for item in handoff.content if item.type == "text")
                assert "Keep the parser API" in text and "Two failures reported" in text
            if phase:
                async with (
                    stdio_client(
                        _server_params(_sanitized_env(bindings[phase - 1]), cwd)
                    ) as streams,
                    ClientSession(*streams) as expired,
                ):
                    await expired.initialize()
                    tools = {tool.name for tool in (await expired.list_tools()).tools}
                    assert "mark_completed" not in tools and "get_task_objective" not in tools

    def run(self: Any, *, argv: list[str], cwd: Path, env: dict[str, str]) -> int:
        phase = len(bindings)
        provider = Path(argv[0]).name
        assert provider == ("claude" if phase in (0, 3) else "codex")
        assert OBJECTIVE not in " ".join(argv)
        binding = _declared_mcp_env(provider, argv)
        assert binding[ENV_SESSION_ID] == env[ENV_SESSION_ID]
        bindings.append(binding)
        self._on_spawn()
        assert desktop.status()["phase"] == "running"
        blocked = desktop.call("plan", action=desktop.action("resume"))
        assert blocked["error"]["code"] == "workspace_busy"
        anyio.run(use_context, binding, phase, cwd)
        return 0

    monkeypatch.setattr(
        "cortexshift.adapters.process_runner.SubprocessInteractiveProcessRunner.run_interactive",
        run,
    )
    sessions = []
    for index, (action, mode) in enumerate(
        [
            (desktop.action("start", "claude"), "start"),
            (desktop.action("switch", "codex"), "switch_fresh"),
            (desktop.action("resume"), "native_resume"),
            (desktop.action("switch", "claude"), "switch_native"),
        ],
        start=1,
    ):
        plan = desktop.call("plan", action=action)["result"]
        assert plan["mode"] == mode
        first = desktop.submit(action, plan, index)
        assert first["result"]["action"]["phase"] == "pending"
        assert desktop.submit(action, plan, index) == first
        assert len(jobs) == index
        assert terminal.run_job(jobs[-1]) == 0
        assert terminal.run_job(jobs[-1]) == 1
        receipt = desktop.status()
        assert receipt["phase"] == "completed" and receipt["spawned"]
        assert bindings[-1][ENV_INVOCATION_TOKEN] not in json.dumps(receipt)
        session = desktop.call("session", record_id=receipt["session_id"])["result"]
        assert session["objective_receipt"]["state"] == "complete"
        assert session["checkpoint"] is not None
        sessions.append(session)

    assert sessions[1]["delivery"]["status"] == "delivered"
    assert sessions[2]["resumed_from_session_id"] == sessions[1]["id"]
    assert sessions[3]["resumed_from_session_id"] == sessions[0]["id"]
    checkpoint = desktop.call("checkpoint", record_id=checkpoint_id)["result"]
    assert checkpoint["test_status"]["provenance"] == "reported"
    with open_workspace(Path(desktop.binding["root"]))[1] as store:
        task = store.get_task(TASK_ID)
        assert task is not None and task.objective == OBJECTIVE
        assert task.completed_items == ["Parser built"]


def test_pending_cancel_unknown_restart_and_failed_provider_retry(
    desktop: Desktop,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs: list[Path] = []
    monkeypatch.setattr(terminal, "_open_terminal", jobs.append)
    assert desktop.create(activate=False)["result"]["active"] is False
    assert desktop.call("overview")["result"]["active_task"] is None
    assert (
        desktop.call("activate_task", task_id=TASK_ID, expected_active=None)["result"]["id"]
        == TASK_ID
    )
    action = desktop.action("start", "claude")
    plan = desktop.call("plan", action=action)["result"]
    with FileWorkspaceLease(Path(desktop.binding["root"]) / ".cortexshift/agent.lock"):
        assert desktop.submit(action, plan, 1)["error"]["code"] == "workspace_busy"
    desktop.submit(action, plan, 1)
    assert desktop.submit(action, plan, 2)["error"]["code"] == "action_blocked"
    desktop.bridge = Bridge()
    assert desktop.status()["phase"] == "pending"
    cancelled = desktop.call("cancel_action", **desktop.spool, action_id=jobs[0].name)
    assert cancelled["result"]["action"]["phase"] == "cancelled"
    assert terminal.run_job(jobs[0]) == 1
    desktop.submit(action, plan, 2)
    # Simulate a worker losing its process after taking the request but before reporting exit.
    (jobs[-1] / "request.json").unlink()
    assert desktop.status()["phase"] == "unknown"
    desktop.submit(action, plan, 3)
    assert terminal.run_job(jobs[-1]) == 1  # Missing fake executable, after Session creation.
    assert desktop.status()["phase"] == "failed"
    assert desktop.call("overview")["result"]["active_task"]["id"] == TASK_ID

    def finish(self: Any, **_: Any) -> int:
        self._on_spawn()
        return 0

    monkeypatch.setattr(
        "cortexshift.adapters.process_runner.SubprocessInteractiveProcessRunner.run_interactive",
        finish,
    )
    desktop.submit(action, plan, 4)
    assert terminal.run_job(jobs[-1]) == 0
    assert desktop.call("tasks")["result"]["items"][0]["session_count"] == 2
    assert len(desktop.call("tasks")["result"]["items"]) == 1
    assert desktop.submit(action, plan, 1)["error"]["code"] == "action_blocked"


def test_explicit_fresh_continuation_and_recovery_gate(desktop: Desktop) -> None:
    desktop.create()
    root, store = open_workspace(Path(desktop.binding["root"]))
    with store:
        old = Session(task_id=TASK_ID, provider_id=PROVIDER_CODEX, status=SessionStatus.COMPLETED)
        store.save_session(old)
    assert (
        desktop.call("plan", action=desktop.action("resume"))["result"]["mode"]
        == "task_continuation"
    )
    exact = desktop.call("plan", action=desktop.action("resume", session_id=old.id))
    assert exact["error"]["code"] == "action_blocked"
    fresh = desktop.call("plan", action=desktop.action("continue", session_id=old.id))["result"]
    assert fresh["source"] == old.id and fresh["mode"] == "task_continuation"

    with open_workspace(root)[1] as store:
        store.save_session(Session(task_id=TASK_ID, provider_id=PROVIDER_CODEX))
    assert (
        desktop.call("plan", action=desktop.action("resume"))["error"]["code"] == "action_blocked"
    )
    assert desktop.call("recover", preview=True)["result"]["uncertain"] == 1
    assert (
        desktop.call("plan", action=desktop.action("start", "bad"))["error"]["code"]
        == "invalid_request"
    )
