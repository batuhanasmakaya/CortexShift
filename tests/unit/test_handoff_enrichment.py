"""Unit tests for Checkpoint-enriched Handoff generation and truth hierarchy."""

from pathlib import Path

from cortexshift.application.handoff_builder import HandoffBuilder
from cortexshift.domain.checkpoint import (
    CheckpointGitState,
    CheckpointKind,
    CheckpointPayload,
    CheckpointRecord,
    CheckpointTaskSnapshot,
    CheckpointTestProvenance,
    CheckpointTestStatus,
)
from cortexshift.domain.git import GitSnapshot, RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.project import Project
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.domain.task import Task


def test_checkpoint_enriches_decisions_and_reported_tests(tmp_path: Path) -> None:
    project = Project(name="Proj", repo_path=str(tmp_path))
    task = Task(
        project_id=project.id,
        title="Current Task Title",
        objective="Current Task Objective",
        completed_items=["live_item_1", "live_item_2"],
    )
    session = Session(task_id=task.id, provider_id=PROVIDER_CLAUDE, status=SessionStatus.COMPLETED)
    inspection = RepositoryInspection(
        project_root=str(tmp_path),
        git_available=True,
        status=RepositoryInspectionStatus.READY,
        snapshot=GitSnapshot(
            project_id=project.id,
            project_root=str(tmp_path),
            git_root=str(tmp_path),
            branch="main",
            head_sha="live_sha_123",
            dirty=True,
            modified_files=["live_mod.py"],
        ),
    )

    # Checkpoint with historical decisions and reported test results
    checkpoint_payload = CheckpointPayload(
        task=CheckpointTaskSnapshot(
            task_id=task.id,
            task_title="Old Stale Title",
            task_status="in_progress",
            objective="Old Stale Objective",
            completed=["stale_item"],
        ),
        git_state=CheckpointGitState(
            status=RepositoryInspectionStatus.READY,
            available=True,
            note="Old git state",
            branch="old_branch",
            head_sha="old_sha",
        ),
        files_touched=["old_file.py"],
        decisions=["Decision 1: Use SQLite", "Decision 2: Zero telemetry"],
        test_status=CheckpointTestStatus(
            known=True,
            summary="42 passed in 1.2s",
            provenance=CheckpointTestProvenance.REPORTED,
        ),
    )
    checkpoint = CheckpointRecord(
        project_id=project.id,
        task_id=task.id,
        session_id=session.id,
        kind=CheckpointKind.MANUAL,
        payload=checkpoint_payload,
    )

    builder = HandoffBuilder()
    payload = builder.build(
        project=project,
        task=task,
        source_session=session,
        target_provider_id=PROVIDER_CODEX,
        inspection=inspection,
        latest_checkpoint=checkpoint,
    )

    # 1. Decisions enriched
    assert payload.decisions_known is True
    assert payload.important_decisions == ["Decision 1: Use SQLite", "Decision 2: Zero telemetry"]

    # 2. Test status enriched with reported provenance and unverified disclaimer
    assert payload.test_status.known is True
    assert "42 passed in 1.2s" in payload.test_status.summary
    assert (
        "reported" in payload.test_status.summary.lower()
        or "unverified" in payload.test_status.summary.lower()
    )

    # 3. Source checkpoint reference linked
    assert payload.source_checkpoint_id == checkpoint.id
    assert payload.source_checkpoint_kind == "manual"

    # 4. Truth hierarchy: CURRENT Task outranks stale Checkpoint Task
    assert payload.original_objective == "Current Task Objective"
    assert payload.task_title == "Current Task Title"
    assert payload.completed == ["live_item_1", "live_item_2"]
    assert "stale_item" not in payload.completed

    # 5. Truth hierarchy: LIVE Git outranks Checkpoint Git
    assert payload.git_state.branch == "main"
    assert payload.git_state.head_sha == "live_sha_123"
    assert "live_mod.py" in payload.files_touched
