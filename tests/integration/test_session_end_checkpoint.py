"""Integration tests for automatic session-end checkpointing across exit conditions."""

import subprocess
from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.session_launcher import ProviderSessionLauncher
from cortexshift.cli.app import app
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import SessionStatus
from cortexshift.domain.task import Task
from cortexshift.ports.process_runner import InteractiveProcessRunner
from tests.cli_runner import AnsiFreeCliRunner


class StubProcessRunner(InteractiveProcessRunner):
    def __init__(self, exit_code: int = 0, raise_exc: BaseException | None = None) -> None:
        self.exit_code = exit_code
        self.raise_exc = raise_exc

    def run_interactive(
        self, argv: list[str], cwd: Path | str, env: dict[str, str] | None = None
    ) -> int:
        if self.raise_exc is not None:
            raise self.raise_exc
        return self.exit_code


def _seed(tmp_path: Path) -> tuple[Project, Task, SQLiteStateStore]:
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=str(tmp_path), check=True
    )
    (tmp_path / "README.md").write_text("# Seed\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(tmp_path), check=True)

    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(db_file)
    project = Project(name="Proj", repo_path=str(tmp_path))
    store.save_project(project)
    task = Task(project_id=project.id, title="Task", objective="Objective")
    store.save_task(task)
    store.set_active_task_id(project.id, task.id)
    return project, task, store


def test_session_end_checkpoint_on_completed_exit(tmp_path: Path) -> None:
    project, task, store = _seed(tmp_path)
    try:
        cp_service = CheckpointService()
        launcher = ProviderSessionLauncher(
            process_runner=StubProcessRunner(exit_code=0),
            store=store,
            checkpoint_service=cp_service,
        )
        spec = LaunchSpecification(
            provider_id=PROVIDER_CLAUDE,
            executable="claude",
            cwd=tmp_path,
            argv=["claude"],
        )
        session = launcher.start_session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
        session = launcher.run(session=session, launch_spec=spec)

        assert session.status == SessionStatus.COMPLETED
        cps = store.list_checkpoints(task_id=task.id)
        assert len(cps) == 1
        assert cps[0].kind == CheckpointKind.SESSION_END
        assert cps[0].session_id == session.id
    finally:
        store.close()


def test_session_end_checkpoint_on_nonzero_failure_exit(tmp_path: Path) -> None:
    project, task, store = _seed(tmp_path)
    try:
        (tmp_path / "work_in_progress.py").write_text("# half finished work\n")
        cp_service = CheckpointService()
        launcher = ProviderSessionLauncher(
            process_runner=StubProcessRunner(exit_code=1),  # e.g. quota or error
            store=store,
            checkpoint_service=cp_service,
        )
        spec = LaunchSpecification(
            provider_id=PROVIDER_CLAUDE,
            executable="claude",
            cwd=tmp_path,
            argv=["claude"],
        )
        session = launcher.start_session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
        session = launcher.run(session=session, launch_spec=spec)

        assert session.status == SessionStatus.FAILED
        assert session.exit_code == 1

        cps = store.list_checkpoints(task_id=task.id)
        assert len(cps) == 1
        assert cps[0].kind == CheckpointKind.SESSION_END
        assert cps[0].session_id == session.id
        assert "work_in_progress.py" in cps[0].payload.files_touched
    finally:
        store.close()


def test_session_end_checkpoint_on_user_interruption(tmp_path: Path) -> None:
    project, task, store = _seed(tmp_path)
    try:
        cp_service = CheckpointService()
        launcher = ProviderSessionLauncher(
            process_runner=StubProcessRunner(raise_exc=KeyboardInterrupt()),
            store=store,
            checkpoint_service=cp_service,
        )
        spec = LaunchSpecification(
            provider_id=PROVIDER_CLAUDE,
            executable="claude",
            cwd=tmp_path,
            argv=["claude"],
        )
        session = launcher.start_session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
        session = launcher.run(session=session, launch_spec=spec)

        assert session.status == SessionStatus.INTERRUPTED
        cps = store.list_checkpoints(task_id=task.id)
        assert len(cps) == 1
        assert cps[0].kind == CheckpointKind.SESSION_END
        assert cps[0].session_id == session.id
    finally:
        store.close()


def test_spawn_failure_does_not_create_checkpoint(tmp_path: Path) -> None:
    project, task, store = _seed(tmp_path)
    try:
        cp_service = CheckpointService()
        launcher = ProviderSessionLauncher(
            process_runner=StubProcessRunner(raise_exc=FileNotFoundError("Binary not found")),
            store=store,
            checkpoint_service=cp_service,
        )
        spec = LaunchSpecification(
            provider_id=PROVIDER_CLAUDE,
            executable="claude",
            cwd=tmp_path,
            argv=["claude"],
        )
        session = launcher.start_session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
        with pytest.raises(FileNotFoundError):
            launcher.run(session=session, launch_spec=spec)

        cps = store.list_checkpoints(task_id=task.id)
        assert len(cps) == 0
    finally:
        store.close()


def test_session_end_checkpoint_preserves_earlier_test_evidence_in_cli_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _, task, store = _seed(tmp_path)
    with store:
        checkpoints = CheckpointService()
        launcher = ProviderSessionLauncher(
            process_runner=StubProcessRunner(), store=store, checkpoint_service=checkpoints
        )
        session = launcher.start_session(task_id=task.id, provider_id=PROVIDER_CLAUDE)
        reported = checkpoints.create_checkpoint(
            start_dir=tmp_path, session_id=session.id, test_summary="pytest: 900 tests passed"
        )
        launcher.run(
            session,
            LaunchSpecification(
                provider_id=PROVIDER_CLAUDE, executable="claude", cwd=tmp_path, argv=["claude"]
            ),
        )
        latest = store.get_latest_checkpoint(task.id)
        assert latest is not None and latest.kind == CheckpointKind.SESSION_END
        assert latest.payload.test_status.known is False

        result = AnsiFreeCliRunner().invoke(app, ["handoff", "preview", "codex"])
        assert result.exit_code == 0
        assert "pytest: 900 tests passed" in result.stdout
        assert reported.id in result.stdout
        assert "matches live HEAD" in " ".join(result.stdout.split())
        assert store.get_checkpoint(reported.id) == reported
