"""Managed switch and resume with fake providers and the real MCP wire protocol."""

import json
import subprocess
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import anyio
import pytest
from mcp import ClientSession
from mcp.client.stdio import stdio_client

from cortexshift.application.switch_service import SwitchService
from cortexshift.application.workspace import open_workspace
from cortexshift.cli.app import app
from cortexshift.domain.handoff import MAX_HANDOFF_BYTES, HandoffStatus
from cortexshift.domain.mcp_binding import (
    ENV_INVOCATION_TOKEN,
    ENV_SESSION_ID,
    ENV_TASK_ID,
    INVOCATION_VERIFIER_KEY,
)
from cortexshift.domain.session import STARTUP_OBJECTIVE_SERVED_KEY
from tests.cli_runner import AnsiFreeCliRunner, unwrapped
from tests.factories import FakeCodexBootstrap, patch_which
from tests.integration.test_managed_mcp_grandchild import (
    _declared_mcp_env,
    _sanitized_env,
    _server_params,
)
from tests.integration.test_start_workflow import launch_runner
from tests.integration.test_start_workflow import project as project


async def _call(
    client: ClientSession, tool: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    response = await client.call_tool(tool, args)
    assert not response.is_error, response.content
    assert response.structured_content is not None
    return cast(dict[str, Any], response.structured_content)


def test_start_switch_resume_and_return(
    project: Path, monkeypatch: pytest.MonkeyPatch, codex_bootstrap: FakeCodexBootstrap
) -> None:
    subprocess.run(["git", "init", "-q", str(project)], check=True, capture_output=True)
    objective = "# Finish the parser\r\n" + '  Keep Unicode 🧪 and "quotes".\r\n' * 2100
    (project / "prompt.md").write_bytes(objective.encode("utf-8"))
    assert len(objective.encode("utf-8")) > MAX_HANDOFF_BYTES
    monkeypatch.setattr(SwitchService, "_check_tty", staticmethod(lambda: True))
    patch_which(monkeypatch, "cortexshift.application.switch_service", lambda cmd: cmd)

    launches: list[dict[str, str]] = []
    histories: list[dict[str, Any]] = []
    decision = "Keep the original parser API."
    checkpoint_id = ""
    claude_native_id = ""

    async def check_expired(binding: dict[str, str]) -> None:
        async with (
            stdio_client(_server_params(_sanitized_env(binding), project)) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            tools = {tool.name for tool in (await client.list_tools()).tools}
            assert tools.isdisjoint({"get_task_objective", "get_task_history", "record_issue"})

    async def use_context(binding: dict[str, str], prompt: str, phase: int) -> None:
        nonlocal checkpoint_id
        if phase:
            await check_expired(launches[phase - 1])
        async with (
            stdio_client(_server_params(_sanitized_env(binding), project)) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            if "get_handoff_context" in prompt:
                handoff = await client.call_tool("get_handoff_context", {})
                assert not handoff.is_error
                body = " ".join(
                    content.text for content in handoff.content if content.type == "text"
                )
                assert "original objective is omitted" in body
                assert "Keep the original parser API." in body
            offset: int | None = 0
            pages: list[str] = []
            while offset is not None:
                page = await _call(client, "get_task_objective", {"offset": offset})
                assert page["task_id"] == binding[ENV_TASK_ID]
                pages.append(page["text"])
                offset = page["next_offset"]
            assert "".join(pages) == objective

            task = await _call(client, "get_current_task", {"include_objective": False})
            assert task["id"] == binding[ENV_TASK_ID] and "objective" not in task
            if phase == 0:
                await _call(client, "mark_completed", {"items": ["Build parser skeleton"]})
                await _call(client, "add_remaining", {"items": ["Handle Unicode input"]})
                await _call(client, "record_issue", {"items": ["Escaped delimiters fail"]})
                checkpoint = await _call(
                    client,
                    "create_checkpoint",
                    {"decisions": [decision], "test_summary": "pytest: 2 failures reported"},
                )
                checkpoint_id = checkpoint["checkpoint_id"]
            else:
                assert task["completed_items"] == ["Build parser skeleton"]
                assert task["remaining_items"] == ["Handle Unicode input"]
                assert task["known_issues"] == ["Escaped delimiters fail"]
                assert "get_task_history" in prompt
                history = await _call(client, "get_task_history")
                assert history["important_decisions"] == [decision]
                evidence = history["historical_test_evidence"]
                assert evidence["checkpoint_id"] == checkpoint_id
                assert evidence["status"]["provenance"] == "reported"
                histories.append(history)

    def fake_provider(*, argv: list[str], cwd: Path, env: dict[str, str]) -> int:
        nonlocal claude_native_id
        assert cwd == project
        phase = len(launches)
        provider = Path(argv[0]).name
        assert provider == ("claude" if phase in (0, 3) else "codex")
        if phase == 0:
            claude_native_id = argv[argv.index("--session-id") + 1]
        elif provider == "claude":
            assert argv[argv.index("--resume") + 1] == claude_native_id
        else:
            assert argv[-3:-1] == ["resume", "test-codex-native-id"]
        prompt = argv[-1]
        assert len(prompt.encode("utf-8")) < 4096
        assert "get_task_objective" in prompt and "Keep Unicode" not in " ".join(argv)
        binding = _declared_mcp_env(provider, argv)
        assert binding[ENV_SESSION_ID] == env[ENV_SESSION_ID]
        assert binding[ENV_TASK_ID] == env[ENV_TASK_ID]
        if launches:
            assert launches[0][ENV_TASK_ID] == binding[ENV_TASK_ID]
        launches.append(binding)
        anyio.run(use_context, binding, prompt, phase)
        return 0

    launch_runner(monkeypatch, Mock(side_effect=fake_provider))
    cli = AnsiFreeCliRunner()
    for cmd, feedback in (
        (["start", "claude", "--file", "prompt.md"], "MCP served the complete Task objective"),
        (["switch", "codex"], "completed read-only bootstrap turn"),
        (["resume"], "Requesting exact native resume in Codex"),
        (
            ["switch", "claude", "--note", "Return to the first conversation"],
            "MCP served the prepared handoff",
        ),
    ):
        result = cli.invoke(app, cmd)
        assert result.exit_code == 0, (result.output, result.exception)
        assert feedback in unwrapped(result.output)
        for binding in launches:
            assert binding[ENV_INVOCATION_TOKEN] not in result.output
    assert len(codex_bootstrap.input_texts) == 1
    bootstrap = codex_bootstrap.input_texts[0]
    assert len(bootstrap.encode("utf-8")) <= MAX_HANDOFF_BYTES
    assert "original objective is omitted" in bootstrap and "Keep Unicode" not in bootstrap
    assert decision in bootstrap and "Escaped delimiters fail" in bootstrap

    workspace, store = open_workspace(project)
    with store:
        task_id = launches[0][ENV_TASK_ID]
        task_record = store.get_task(task_id)
        assert task_record is not None and task_record.objective == objective
        assert store.get_active_task_id(workspace.project.id) == task_id
        assert len(store.list_tasks(workspace.project.id)) == 1
        first, switched, resumed, returned = reversed(store.list_sessions(task_id=task_id))
        assert first.native_session_id == returned.native_session_id == claude_native_id
        assert returned.resumed_from_session_id == first.id
        assert resumed.resumed_from_session_id == switched.id
        assert resumed.native_session_id == switched.native_session_id == "test-codex-native-id"
        for index, session in enumerate((first, switched, resumed, returned)):
            assert session.id == launches[index][ENV_SESSION_ID]
            assert session.task_id == task_id
            assert session.metadata[STARTUP_OBJECTIVE_SERVED_KEY] == len(objective)
            assert INVOCATION_VERIFIER_KEY not in session.metadata
        back, forward = store.list_handoffs(task_id=task_id)
        assert (
            histories[0]["latest_handoff"]["id"]
            == histories[1]["latest_handoff"]["id"]
            == forward.id
        )
        assert histories[2]["latest_handoff"]["id"] == back.id
        assert back.status == forward.status == HandoffStatus.DELIVERED
        assert forward.target_session_id == switched.id and forward.source_session_id == first.id
        assert back.target_session_id == returned.id and back.source_session_id == resumed.id
        assert forward.payload.original_objective == back.payload.original_objective == objective
        assert forward.source_checkpoint_id is not None
        checkpoint = store.get_checkpoint(checkpoint_id)
        assert checkpoint is not None and checkpoint.session_id == first.id
        assert store.get_schema_version() == 6
    anyio.run(check_expired, launches[-1])

    status = json.loads(cli.invoke(app, ["status", "--json"]).stdout)
    assert status["latest_session"]["objective_retrieval"] == "complete"
    assert status["latest_session"]["handoff_status"] == "delivered"
    assert status["workspace_busy"] is False
    assert status["unfinalized_sessions"] == 0


@pytest.mark.parametrize("serve_page", [False, True], ids=["unconfirmed", "partial"])
def test_fresh_resume_reports_only_objective_pages_served(
    project: Path, monkeypatch: pytest.MonkeyPatch, serve_page: bool
) -> None:
    objective = "Long objective\n" + "🧪" * 5000
    launch_runner(monkeypatch, Mock(return_value=0))
    cli = AnsiFreeCliRunner()
    started = cli.invoke(app, ["start", "codex", "--objective", objective])
    assert started.exit_code == 0, started.output
    initial = json.loads(cli.invoke(app, ["status", "--json"]).stdout)["latest_session"]
    assert initial["objective_retrieval"] == "unconfirmed"

    async def read_first_page(binding: dict[str, str]) -> None:
        async with (
            stdio_client(_server_params(_sanitized_env(binding), project)) as (read, write),
            ClientSession(read, write) as client,
        ):
            await client.initialize()
            page = await _call(client, "get_task_objective")
            assert page["text"] == objective[:4000]
            assert page["next_offset"] == 4000

    def fake_provider(*, argv: list[str], cwd: Path, env: dict[str, str]) -> int:
        assert cwd == project
        binding = _declared_mcp_env("codex", argv)
        assert binding[ENV_SESSION_ID] == env[ENV_SESSION_ID] != initial["id"]
        assert "resume" not in argv and "get_task_objective" in argv[-1]
        if serve_page:
            anyio.run(read_first_page, binding)
        return 0

    launch_runner(monkeypatch, Mock(side_effect=fake_provider))
    outcome = cli.invoke(app, ["resume"])
    assert outcome.exit_code == 0, outcome.output
    assert "Starting a fresh conversation with the same Task" in unwrapped(outcome.output)
    expected = "partial" if serve_page else "unconfirmed"
    assert ("part of the Task objective" in unwrapped(outcome.output)) is serve_page
    assert "retrieval is unconfirmed" in unwrapped(outcome.output)
    status = json.loads(cli.invoke(app, ["status", "--json"]).stdout)
    session = status["latest_session"]
    assert session["objective_retrieval"] == expected
    assert session["continued_from_session_id"] == initial["id"]
    assert session["resumed_from_session_id"] is None
