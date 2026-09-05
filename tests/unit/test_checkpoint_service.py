"""Unit tests for CheckpointService application logic."""

from pathlib import Path

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.domain.checkpoint import CheckpointKind, CheckpointTestProvenance
from cortexshift.domain.errors import (
    GitProbeError,
    SessionNotFoundError,
    SessionTaskMismatchError,
)
from cortexshift.domain.git import GitSnapshot, RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task
from cortexshift.ports.repository import RepositoryInspector


class MockInspector(RepositoryInspector):
    def __init__(self, inspection: RepositoryInspection | None = None) -> None:
        self.inspection = inspection

    def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
        if self.inspection is not None:
            return self.inspection
        snapshot = GitSnapshot(
            project_id=project_id,
            project_root=str(project_root),
            git_root=str(project_root),
            branch="main",
            dirty=False,
        )
        return RepositoryInspection(
            project_root=str(project_root),
            git_available=True,
            status=RepositoryInspectionStatus.READY,
            snapshot=snapshot,
        )


def _seed(tmp_path: Path) -> tuple[Project, Task, Session]:
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    db_file.parent.mkdir(parents=True, exist_ok=True)
    with SQLiteStateStore(db_file) as store:
        project = Project(name="TestProj", repo_path=str(tmp_path))
        store.save_project(project)
        task = Task(project_id=project.id, title="Task A", objective="Objective A")
        store.save_task(task)
        store.set_active_task_id(project.id, task.id)
        session = Session(
            task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.COMPLETED
        )
        store.save_session(session)
    return project, task, session


def test_create_checkpoint_happy_path(tmp_path: Path) -> None:
    project, task, session = _seed(tmp_path)
    service = CheckpointService(inspector=MockInspector())

    cp = service.create_checkpoint(
        decisions=["Decision 1"],
        test_summary="All pass",
        test_provenance=CheckpointTestProvenance.REPORTED,
        note="Manual milestone",
        start_dir=tmp_path,
    )

    assert cp.id.startswith("cp_")
    assert cp.kind == CheckpointKind.MANUAL
    assert cp.task_id == task.id
    assert cp.session_id == session.id
    assert cp.git_snapshot_id is not None
    assert cp.payload.decisions == ["Decision 1"]
    assert cp.payload.operator_note == "Manual milestone"
    assert cp.payload.test_status.summary == "All pass"
    assert cp.payload.test_status.provenance == CheckpointTestProvenance.REPORTED

    # Verify query methods
    assert service.get_checkpoint(cp.id, start_dir=tmp_path) is not None
    latest = service.get_latest_checkpoint(task.id, start_dir=tmp_path)
    assert latest is not None
    assert latest.id == cp.id
    all_cps = service.list_checkpoints(task_id=task.id, start_dir=tmp_path)
    assert len(all_cps) == 1


def test_create_checkpoint_session_mismatch_and_not_found(tmp_path: Path) -> None:
    project, task, session = _seed(tmp_path)
    db_file = tmp_path / ".cortexshift" / "state.sqlite3"

    # Create second task and session
    with SQLiteStateStore(db_file) as store:
        task2 = Task(project_id=project.id, title="Task 2", objective="Objective 2")
        store.save_task(task2)
        session2 = Session(task_id=task2.id, provider_id=PROVIDER_CLAUDE)
        store.save_session(session2)

    service = CheckpointService(inspector=MockInspector())

    # Passing session from different task
    with pytest.raises(SessionTaskMismatchError):
        service.create_checkpoint(session_id=session2.id, start_dir=tmp_path)

    # Passing non-existent session
    with pytest.raises(SessionNotFoundError):
        service.create_checkpoint(session_id="sess_missing", start_dir=tmp_path)


def test_create_checkpoint_fails_on_git_probe_error(tmp_path: Path) -> None:
    _seed(tmp_path)
    bad_inspection = RepositoryInspection(
        project_root=str(tmp_path),
        git_available=False,
        status=RepositoryInspectionStatus.PROBE_ERROR,
        diagnostic="Fatal: corrupt git index",
    )
    service = CheckpointService(inspector=MockInspector(bad_inspection))

    with pytest.raises(GitProbeError, match="corrupt git index"):
        service.create_checkpoint(start_dir=tmp_path)


def test_capture_session_end_checkpoint_success_and_safe_failure(tmp_path: Path) -> None:
    project, task, session = _seed(tmp_path)
    service = CheckpointService(inspector=MockInspector())

    db_file = tmp_path / ".cortexshift" / "state.sqlite3"
    with SQLiteStateStore(db_file) as store:
        record = service.capture_session_end_checkpoint(session=session, store=store)
        assert record is not None
        assert record.kind == CheckpointKind.SESSION_END
        assert record.session_id == session.id

    # Probe error scenario: does not raise, logs warning, attaches metadata
    bad_inspection = RepositoryInspection(
        project_root=str(tmp_path),
        git_available=False,
        status=RepositoryInspectionStatus.PROBE_ERROR,
        diagnostic="Corrupt repository",
    )
    service_probe_err = CheckpointService(inspector=MockInspector(bad_inspection))
    with SQLiteStateStore(db_file) as store:
        failed_record = service_probe_err.capture_session_end_checkpoint(
            session=session, store=store
        )
        assert failed_record is None
        # Session outcome is preserved
        refreshed_session = store.get_session(session.id)
        assert refreshed_session is not None
        assert refreshed_session.status == SessionStatus.COMPLETED
        assert refreshed_session.metadata.get("checkpoint_capture_warning") == "git_probe_error"
