"""Flagship integration test for Hard Crash Recovery and subsequent provider handoff."""

import json
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from cortexshift.adapters.providers.claude import ClaudeHandoffAdapter
from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.switch_service import ProviderHandoffRegistry, SwitchService
from cortexshift.cli.app import app
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.handoff import HandoffStatus
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.ports.headless_runner import HeadlessProviderRunner, HeadlessResult
from cortexshift.ports.process_runner import InteractiveProcessRunner

runner = CliRunner()


class FakeHeadless(HeadlessProviderRunner):
    def run_headless(self, *args, **kwargs) -> HeadlessResult:
        output = "\n".join(
            [
                json.dumps(
                    {"type": "thread.started", "thread_id": "12345678-1234-4234-8234-123456789abc"}
                ),
                '{"type":"turn.completed"}',
            ]
        )
        return HeadlessResult(exit_code=0, stdout=output, stderr="")


class FakeInteractive(InteractiveProcessRunner):
    def run_interactive(
        self, argv: list[str], cwd: Path | str, env: dict[str, str] | None = None
    ) -> int:
        return 0


def _seed_git_repo(tmp_path: Path) -> tuple[Project, Task]:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True
    )
    (tmp_path / "README.md").write_text("# Project\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), check=True)

    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(db_file) as store:
        project = Project(name="CrashProj", repo_path=str(tmp_path))
        store.save_project(project)
        task = Task(
            project_id=project.id,
            title="Crash Recovery Task",
            objective="Recover safely after hard crash",
            requirements=["Crash resilience"],
        )
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)
    return project, task


def test_hard_crash_recovery_flagship(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project, task = _seed_git_repo(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    # 1. Agent starts working: Session is in RUNNING state
    with SQLiteStateStore(db_file) as store:
        crashed_session = Session(
            task_id=task.id,
            provider_id=PROVIDER_CLAUDE,
            status=SessionStatus.RUNNING,
            native_session_id="claude-crashed-native",
        )
        store.save_session(crashed_session)

    # 2. Agent writes uncommitted code to working tree
    (tmp_path / "critical_work.py").write_text("# half finished critical algorithm\n")

    # 3. Simulate sudden termination: process terminates, lock is released,
    # but DB record stays RUNNING
    lease = FileWorkspaceLeaseManager().get_lease(tmp_path)
    assert lease.is_locked() is False

    # 4. Invoke cortexshift recover
    rec_result = runner.invoke(app, ["recover", "--json"])
    assert rec_result.exit_code == 0
    report = json.loads(rec_result.stdout)

    assert report["stale_session_ids"] == [crashed_session.id]
    assert report["reconciled_session_ids"] == [crashed_session.id]
    assert report["checkpoint_id"].startswith("cp_")
    assert report["dirty"] is True
    assert "critical_work.py" in report["files_touched"]

    # Verify honest reconciliation in database
    with SQLiteStateStore(db_file) as store:
        reconciled = store.get_session(crashed_session.id)
        assert reconciled is not None
        assert reconciled.status == SessionStatus.INTERRUPTED
        assert reconciled.exit_reason == SessionExitReason.UNEXPECTED_TERMINATION
        assert reconciled.ended_at is None  # Never fabricated!
        assert reconciled.reconciled_at is not None

        # Verify recovery checkpoint was created
        recovery_cp = store.get_checkpoint(report["checkpoint_id"])
        assert recovery_cp is not None
        assert recovery_cp.kind == CheckpointKind.RECOVERY
        assert recovery_cp.session_id == crashed_session.id
        assert "critical_work.py" in recovery_cp.payload.files_touched

    # 5. Subsequent switch to Codex succeeds with enriched handoff
    registry = ProviderHandoffRegistry(
        [
            ClaudeHandoffAdapter(),
            CodexHandoffAdapter(headless_runner=FakeHeadless()),
        ]
    )
    switch_service = SwitchService(
        registry=registry,
        process_runner=FakeInteractive(),
        which_fn=lambda cmd: "/fake/" + cmd,
        is_tty_fn=lambda: True,
    )

    switch_result = switch_service.switch("codex", start_dir=tmp_path)
    assert switch_result.handoff.status == HandoffStatus.DELIVERED
    assert switch_result.handoff.source_checkpoint_id == report["checkpoint_id"]
    assert switch_result.handoff.payload.source_checkpoint_id == report["checkpoint_id"]
    assert switch_result.handoff.payload.source_checkpoint_kind == "recovery"
    assert switch_result.target_session.provider_id == PROVIDER_CODEX
