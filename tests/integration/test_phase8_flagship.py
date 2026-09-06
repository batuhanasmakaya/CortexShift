"""Comprehensive Flagship Integration Test for Phase 8:
MCP Shared State, Agent Self-Reporting, and Cross-Agent Continuity.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cortexshift.adapters.providers.antigravity import AntigravityHandoffAdapter
from cortexshift.adapters.providers.claude import ClaudeHandoffAdapter
from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.adapters.sqlite.migrations import CURRENT_SCHEMA_VERSION
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.run_service import RunService
from cortexshift.application.switch_service import ProviderHandoffRegistry, SwitchService
from cortexshift.domain.handoff import HandoffStatus
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import SessionStatus
from cortexshift.domain.task import Task
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.facade import McpApplicationFacade
from cortexshift.ports.headless_runner import HeadlessProviderRunner, HeadlessResult
from cortexshift.ports.process_runner import InteractiveProcessRunner

runner = CliRunner()


class RecordingProcessRunner(InteractiveProcessRunner):
    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.invocations: list[dict[str, Any]] = []

    def run_interactive(
        self, argv: list[str], cwd: Path | str, env: dict[str, str] | None = None
    ) -> int:
        self.invocations.append({"argv": argv, "cwd": cwd, "env": env})
        return self.exit_code


class FakeBootstrap(HeadlessProviderRunner):
    def run_headless(
        self,
        argv: list[str],
        cwd: Path | str,
        timeout: float = 30.0,
        env: dict[str, str] | None = None,
    ) -> HeadlessResult:
        if "codex" in argv[0]:
            output = "\n".join(
                [
                    json.dumps(
                        {
                            "type": "thread.started",
                            "thread_id": "88888888-8888-4888-8888-888888888888",
                        }
                    ),
                    '{"type":"turn.completed"}',
                ]
            )
        else:
            output = json.dumps(
                {"conversation_id": "99999999-9999-4999-8999-999999999999", "status": "SUCCESS"}
            )
        return HeadlessResult(exit_code=0, stdout=output, stderr="")


def _seed_git_repo(tmp_path: Path) -> tuple[Project, Task]:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True
    )
    (tmp_path / "README.md").write_text("# Flagship Phase 8 Repository\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), check=True)

    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(db_file) as store:
        project = Project(name="Phase8FlagshipProj", repo_path=str(tmp_path))
        store.save_project(project)
        task = Task(
            project_id=project.id,
            title="Phase 8 MCP Flagship Task",
            objective="Demonstrate complete MCP agent self-reporting and handoff enrichment",
            requirements=["MCP stdio server", "Task self-reporting", "Cross-agent continuity"],
            completed_items=["Phase 7 baseline"],
            remaining_items=["Self-reported progress", "Handoff to Codex"],
        )
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)
    return project, task


def test_phase8_mcp_flagship_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    project, task = _seed_git_repo(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    process_runner = RecordingProcessRunner(exit_code=0)
    bootstrap = FakeBootstrap()

    registry = ProviderHandoffRegistry(
        [
            ClaudeHandoffAdapter(),
            CodexHandoffAdapter(headless_runner=bootstrap),
            AntigravityHandoffAdapter(headless_runner=bootstrap),
        ]
    )
    run_service = RunService(
        process_runner=process_runner,
        which_fn=lambda cmd: "/fake/" + cmd,
        is_tty_fn=lambda: True,
    )
    switch_service = SwitchService(
        registry=registry,
        process_runner=process_runner,
        which_fn=lambda cmd: "/fake/" + cmd,
        is_tty_fn=lambda: True,
    )

    # -------------------------------------------------------------------------
    # 1. Agent 1 (Claude Code) runs
    # -------------------------------------------------------------------------
    s1 = run_service.run("claude", start_dir=tmp_path)
    assert s1.status == SessionStatus.COMPLETED
    assert len(process_runner.invocations) == 1

    inv1 = process_runner.invocations[0]
    assert "--mcp-config" in inv1["argv"]
    idx = inv1["argv"].index("--mcp-config")
    claude_mcp_cfg = json.loads(inv1["argv"][idx + 1])
    assert "cortexshift" in claude_mcp_cfg["mcpServers"]
    assert inv1["env"]["CORTEXSHIFT_TASK_ID"] == task.id
    assert inv1["env"]["CORTEXSHIFT_SESSION_ID"] == s1.id
    assert inv1["env"]["CORTEXSHIFT_MCP_READ_ONLY"] == "0"

    # -------------------------------------------------------------------------
    # 2. Claude interacts with CortexShift MCP during session
    # -------------------------------------------------------------------------
    store = SQLiteStateStore(db_file)
    ctx1 = McpExecutionContext(
        project_root=tmp_path,
        project_id=project.id,
        task_id=task.id,
        session_id=s1.id,
        provider_id=PROVIDER_CLAUDE,
        managed_session=True,
        read_only=False,
    )
    facade1 = McpApplicationFacade(ctx1, store)

    # Agent reports current work
    facade1.set_current_work("Implementing MCP server architecture")

    # Agent adds more remaining items discovered during work
    facade1.add_remaining(
        ["Self-reported progress", "Subprocess stdio test", "Wire protocol verification"]
    )

    # Agent marks item completed (removes from remaining, does not finish whole task!)
    facade1.mark_completed(["Self-reported progress", "Subprocess stdio test"])

    # Agent records known issue
    facade1.record_issue(["Minor stdio buffer flush nuance"])

    # Agent records architectural decision
    dec_res = facade1.record_decision("Adopted stdio transport for local MCP communication")
    assert dec_res.checkpoint_id.startswith("cp_")

    # Agent captures milestone checkpoint
    cp_res = facade1.create_checkpoint(
        decisions=[
            "Adopted stdio transport for local MCP communication",
            "Enforce pure stdout wire protocol",
        ],
        test_summary="540 passed in 18s",
        note="Claude implementation complete, ready for handoff",
    )
    assert cp_res.checkpoint_id.startswith("cp_")
    store.close()

    # -------------------------------------------------------------------------
    # 3. Switch to Agent 2 (Codex): verify handoff includes MCP-reported state
    # -------------------------------------------------------------------------
    switch_res = switch_service.switch("codex", start_dir=tmp_path)
    assert switch_res.handoff.status == HandoffStatus.DELIVERED
    payload = switch_res.handoff.payload

    assert payload.current_work == "Implementing MCP server architecture"
    assert "Self-reported progress" in payload.completed
    assert "Subprocess stdio test" in payload.completed
    assert "Wire protocol verification" in payload.remaining
    assert "Self-reported progress" not in payload.remaining
    assert "Minor stdio buffer flush nuance" in payload.known_issues
    assert "Adopted stdio transport for local MCP communication" in payload.important_decisions
    assert "Enforce pure stdout wire protocol" in payload.important_decisions
    assert "540 passed in 18s" in payload.test_status.summary
    assert payload.test_status.known is True

    # Verify Codex launch specification includes -c MCP overrides
    assert len(process_runner.invocations) == 2
    inv2 = process_runner.invocations[1]
    assert "-c" in inv2["argv"]
    c_indices = [i for i, a in enumerate(inv2["argv"]) if a == "-c"]
    c_values = [inv2["argv"][i + 1] for i in c_indices]
    assert f'mcp_servers.cortexshift.command="{sys.executable}"' in c_values

    # -------------------------------------------------------------------------
    # 4. Codex resumes work and updates state via MCP
    # -------------------------------------------------------------------------
    s2_id = switch_res.target_session.id
    store2 = SQLiteStateStore(db_file)
    ctx2 = McpExecutionContext(
        project_root=tmp_path,
        project_id=project.id,
        task_id=task.id,
        session_id=s2_id,
        provider_id=PROVIDER_CODEX,
        managed_session=True,
        read_only=False,
    )
    facade2 = McpApplicationFacade(ctx2, store2)

    # Codex reads task context and latest checkpoint
    task_view = facade2.get_current_task()
    assert task_view["current_work"] == "Implementing MCP server architecture"
    assert "Wire protocol verification" in task_view["remaining_items"]

    chk_view = facade2.get_latest_checkpoint()
    assert chk_view.checkpoint is not None
    # S2 session completion automatically captured a SESSION_END checkpoint
    assert chk_view.checkpoint["kind"] == "session_end"
    assert chk_view.checkpoint["session_id"] == s2_id

    # Claude's milestone checkpoint is preserved in checkpoint history
    claude_cp = store2.get_checkpoint(cp_res.checkpoint_id)
    assert claude_cp is not None
    assert claude_cp.payload.test_status.summary == "540 passed in 18s"
    assert claude_cp.payload.test_status.known is True

    # Codex finishes the remaining items
    facade2.mark_completed(["Wire protocol verification", "Handoff to Codex"])
    facade2.set_current_work(None)
    store2.close()

    # -------------------------------------------------------------------------
    # 5. Invariants and final state verification
    # -------------------------------------------------------------------------
    final_store = SQLiteStateStore(db_file)
    final_task = final_store.get_task(task.id)
    assert final_task is not None
    assert final_task.current_work is None
    assert "Wire protocol verification" in final_task.completed
    assert "Handoff to Codex" in final_task.completed
    assert len(final_task.remaining) == 0

    # Schema version remains strictly v6
    assert final_store.get_schema_version() == 6
    assert CURRENT_SCHEMA_VERSION == 6

    # Lock is completely free
    lease = FileWorkspaceLeaseManager().get_lease(tmp_path)
    assert lease.is_locked() is False
    final_store.close()
