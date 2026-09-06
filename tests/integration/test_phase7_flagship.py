"""Comprehensive Flagship Integration Test for Phase 7:
Checkpoints, Crash Recovery, Handoff Enrichment, and Process Restart Survival.
"""

import json
import subprocess
from pathlib import Path

from cortexshift.adapters.providers.antigravity import AntigravityHandoffAdapter
from cortexshift.adapters.providers.claude import ClaudeHandoffAdapter
from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.adapters.sqlite.migrations import CURRENT_SCHEMA_VERSION
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.run_service import RunService
from cortexshift.application.switch_service import ProviderHandoffRegistry, SwitchService
from cortexshift.cli.app import app
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.handoff import HandoffStatus
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.ports.headless_runner import HeadlessProviderRunner, HeadlessResult
from cortexshift.ports.process_runner import InteractiveProcessRunner
from tests.cli_runner import AnsiFreeCliRunner

runner = AnsiFreeCliRunner()


class RecordingProcessRunner(InteractiveProcessRunner):
    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.invocations: list[list[str]] = []

    def run_interactive(
        self, argv: list[str], cwd: Path | str, env: dict[str, str] | None = None
    ) -> int:
        self.invocations.append(argv)
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
                            "thread_id": "11111111-1111-4111-8111-111111111111",
                        }
                    ),
                    '{"type":"turn.completed"}',
                ]
            )
        else:  # Antigravity
            output = json.dumps(
                {"conversation_id": "22222222-2222-4222-8222-222222222222", "status": "SUCCESS"}
            )
        return HeadlessResult(exit_code=0, stdout=output, stderr="")


def _seed_git_repo(tmp_path: Path) -> tuple[Project, Task]:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True
    )
    (tmp_path / "README.md").write_text("# Flagship Phase 7\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), check=True)

    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(db_file) as store:
        project = Project(name="FlagshipProj", repo_path=str(tmp_path))
        store.save_project(project)
        task = Task(
            project_id=project.id,
            title="Flagship Task",
            objective="Deliver full Phase 7 resilience",
            requirements=["Checkpoints", "Crash Recovery", "Handoff Enrichment"],
            completed_items=["Phase 6 baseline"],
            current_work="Phase 7 flagship",
            remaining_items=["Quality gates"],
        )
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)
    return project, task


def test_full_phase7_resilience_and_restart_flagship(tmp_path: Path, monkeypatch) -> None:
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

    # 1. First provider (Claude) runs
    s1 = run_service.run("claude", start_dir=tmp_path)
    assert s1.status == SessionStatus.COMPLETED

    # Verify session-end checkpoint automatically captured for Claude
    with SQLiteStateStore(db_file) as store:
        claude_end_cps = store.list_checkpoints(task_id=task.id)
        assert len(claude_end_cps) == 1
        assert claude_end_cps[0].kind == CheckpointKind.SESSION_END
        assert claude_end_cps[0].session_id == s1.id

    # 2. Simulate agent creating a cooperative manual checkpoint
    (tmp_path / "step1.py").write_text("# completed milestone 1\n")
    cp_manual_res = runner.invoke(
        app,
        [
            "checkpoint",
            "create",
            "-d",
            "Decision: Store structured checkpoints",
            "-t",
            "50 passed in 0.5s",
            "-n",
            "Cooperative milestone by agent",
            "--json",
        ],
    )
    assert cp_manual_res.exit_code == 0
    manual_cp = json.loads(cp_manual_res.stdout)
    assert manual_cp["kind"] == "manual"

    # 3. Simulate hard crash during Codex work:
    # A Session record is created with status RUNNING, working tree modified,
    # but process abruptly dies.
    with SQLiteStateStore(db_file) as store:
        crashed_session = Session(
            task_id=task.id,
            provider_id=PROVIDER_CODEX,
            status=SessionStatus.RUNNING,
            native_session_id="11111111-1111-4111-8111-111111111111",
        )
        store.save_session(crashed_session)

    (tmp_path / "uncommitted_work.py").write_text("# work before crash\n")

    # 4. Invoke recover command
    rec_res = runner.invoke(app, ["recover", "--json"])
    assert rec_res.exit_code == 0
    rec_report = json.loads(rec_res.stdout)
    assert rec_report["stale_session_ids"] == [crashed_session.id]
    assert rec_report["reconciled_session_ids"] == [crashed_session.id]
    assert rec_report["checkpoint_id"].startswith("cp_")
    assert rec_report["dirty"] is True
    assert "uncommitted_work.py" in rec_report["files_touched"]

    # Verify session honestly reconciled
    with SQLiteStateStore(db_file) as store:
        reconciled_s = store.get_session(crashed_session.id)
        assert reconciled_s is not None
        assert reconciled_s.status == SessionStatus.INTERRUPTED
        assert reconciled_s.exit_reason == SessionExitReason.UNEXPECTED_TERMINATION
        assert reconciled_s.ended_at is None
        assert reconciled_s.reconciled_at is not None

        # Verify recovery checkpoint exists
        rec_cp = store.get_checkpoint(rec_report["checkpoint_id"])
        assert rec_cp is not None
        assert rec_cp.kind == CheckpointKind.RECOVERY

    # 5. Switch to Antigravity: receives handoff enriched from recovery checkpoint!
    switch_res = switch_service.switch("antigravity", start_dir=tmp_path)
    assert switch_res.handoff.status == HandoffStatus.DELIVERED
    assert switch_res.handoff.source_checkpoint_id == rec_report["checkpoint_id"]
    assert switch_res.handoff.payload.source_checkpoint_id == rec_report["checkpoint_id"]
    assert switch_res.handoff.payload.source_checkpoint_kind == "recovery"
    assert switch_res.target_session.provider_id == PROVIDER_ANTIGRAVITY

    # 6. Full Restart Simulation (Section 94):
    # Reopen state from an entirely fresh SQLiteStateStore instance and verify
    # all observations survive
    with SQLiteStateStore(db_file) as store:
        assert store.get_schema_version() == CURRENT_SCHEMA_VERSION == 6

        reloaded_proj = store.get_default_project()
        assert reloaded_proj is not None
        assert reloaded_proj.id == project.id

        reloaded_task = store.get_task(task.id)
        assert reloaded_task is not None
        assert reloaded_task.id == task.id
        assert store.get_active_task_id(reloaded_proj.id) == task.id

        # Verify all sessions survived
        sessions = store.list_sessions(task_id=task.id, limit=None)
        assert len(sessions) >= 3
        # Check honest reconciliation survived restart
        crashed_check = store.get_session(crashed_session.id)
        assert crashed_check is not None
        assert crashed_check.status == SessionStatus.INTERRUPTED
        assert crashed_check.reconciled_at is not None

        # Verify all checkpoints survived
        cps = store.list_checkpoints(task_id=task.id, limit=None)
        kinds = {cp.kind for cp in cps}
        assert CheckpointKind.MANUAL in kinds
        assert CheckpointKind.SESSION_END in kinds
        assert CheckpointKind.RECOVERY in kinds

        # Verify handoffs survived with checkpoint reference
        handoffs = store.list_handoffs(project_id=project.id)
        assert len(handoffs) >= 1
        assert any(h.source_checkpoint_id == rec_report["checkpoint_id"] for h in handoffs)

        # Verify no tokens, credentials, transcripts, or prompts stored
        forbidden = ["token", "secret", "password", "credential", "transcript", "reasoning"]
        for cp in cps:
            cp_str = cp.model_dump_json().lower()
            for word in forbidden:
                assert word not in cp_str or word == "reasoning" and "reasoning" not in cp_str
