"""Unit tests for Checkpoint Protocol v1 domain models, enums, limits, and immutability."""

from datetime import UTC

import pytest
from pydantic import ValidationError

from cortexshift.application.checkpoint_builder import CheckpointBuilder
from cortexshift.domain.checkpoint import (
    CHECKPOINT_PROTOCOL_VERSION,
    MAX_DECISION_CHARS,
    MAX_OPERATOR_NOTE_CHARS,
    MAX_TEST_SUMMARY_CHARS,
    CheckpointGitState,
    CheckpointKind,
    CheckpointPayload,
    CheckpointRecord,
    CheckpointSourceSession,
    CheckpointTaskSnapshot,
    CheckpointTestProvenance,
    CheckpointTestStatus,
)
from cortexshift.domain.errors import InvalidCheckpointInputError
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.project import Project
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import SessionExitReason, SessionStatus
from cortexshift.domain.task import Task


def _make_sample_payload() -> CheckpointPayload:
    return CheckpointPayload(
        task=CheckpointTaskSnapshot(
            task_id="task_1",
            task_title="Feature Task",
            task_status="in_progress",
            objective="Implement Phase 7",
            requirements=["req1"],
            constraints=["con1"],
            completed=["item1"],
            current_work="active item",
            remaining=["item2"],
            known_issues=["issue1"],
        ),
        git_state=CheckpointGitState(
            status=RepositoryInspectionStatus.READY,
            available=True,
            note="Git ready",
            branch="feature",
            head_sha="1234567890ab",
            dirty=True,
            staged_count=1,
            modified_count=2,
            untracked_count=0,
            working_tree_diff_summary="2 files changed",
            staged_diff_summary="1 file staged",
            snapshot_id="snap_1",
        ),
        files_touched=["file1.py", "file2.py"],
        decisions=["Decision A"],
        test_status=CheckpointTestStatus(
            known=True,
            summary="5 passed in 0.1s",
            provenance=CheckpointTestProvenance.REPORTED,
        ),
        operator_note="Sample note",
        source_session=CheckpointSourceSession(
            session_id="sess_1",
            provider_id=ProviderId("claude"),
            status=SessionStatus.COMPLETED,
            exit_reason=SessionExitReason.NORMAL_COMPLETION,
            exit_code=0,
        ),
    )


def test_checkpoint_payload_protocol_version_and_defaults() -> None:
    payload = _make_sample_payload()
    assert payload.protocol_version == CHECKPOINT_PROTOCOL_VERSION == 1
    assert payload.generated_at.tzinfo == UTC
    assert payload.task.task_id == "task_1"
    assert payload.git_state.branch == "feature"
    assert payload.files_touched == ["file1.py", "file2.py"]
    assert payload.decisions == ["Decision A"]
    assert payload.test_status.provenance == CheckpointTestProvenance.REPORTED
    assert payload.operator_note == "Sample note"


def test_checkpoint_record_creation_and_immutability() -> None:
    payload = _make_sample_payload()
    record = CheckpointRecord(
        project_id="proj_1",
        task_id="task_1",
        session_id="sess_1",
        git_snapshot_id="snap_1",
        kind=CheckpointKind.MANUAL,
        payload=payload,
    )
    assert record.id.startswith("cp_")
    assert record.protocol_version == 1
    assert record.created_at.tzinfo == UTC
    assert record.kind == CheckpointKind.MANUAL

    # Frozen immutability
    with pytest.raises(ValidationError):
        record.session_id = "sess_modified"

    with pytest.raises(ValidationError):
        record.kind = CheckpointKind.RECOVERY

    with pytest.raises(ValidationError):
        payload.operator_note = "new note"


def test_checkpoint_serialization_roundtrip() -> None:
    payload = _make_sample_payload()
    record = CheckpointRecord(
        project_id="proj_1",
        task_id="task_1",
        session_id="sess_1",
        git_snapshot_id="snap_1",
        kind=CheckpointKind.SESSION_END,
        payload=payload,
    )
    json_str = record.model_dump_json()
    restored = CheckpointRecord.model_validate_json(json_str)
    assert restored == record
    assert restored.payload.task.completed == ["item1"]
    assert restored.payload.git_state.head_sha == "1234567890ab"


def test_checkpoint_kind_enum_values() -> None:
    assert CheckpointKind.MANUAL.value == "manual"
    assert CheckpointKind.SESSION_END.value == "session_end"
    assert CheckpointKind.RECOVERY.value == "recovery"


def test_checkpoint_test_provenance_values() -> None:
    assert CheckpointTestProvenance.UNKNOWN.value == "unknown"
    assert CheckpointTestProvenance.REPORTED.value == "reported"
    assert CheckpointTestProvenance.VERIFIED.value == "verified"


def test_checkpoint_input_length_limits() -> None:
    project = Project(name="Proj", repo_path="/repo")
    task = Task(project_id=project.id, title="Title", objective="Obj")
    inspection = RepositoryInspection(
        project_root="/repo",
        git_available=False,
        status=RepositoryInspectionStatus.GIT_NOT_INSTALLED,
    )

    # Note at limit passes
    note_exact = "x" * MAX_OPERATOR_NOTE_CHARS
    cp_ok = CheckpointBuilder.build(
        project=project,
        task=task,
        inspection=inspection,
        kind=CheckpointKind.MANUAL,
        operator_note=note_exact,
    )
    assert cp_ok.payload.operator_note == note_exact

    # Note over limit fails
    with pytest.raises(InvalidCheckpointInputError, match="Operator note exceeds"):
        CheckpointBuilder.build(
            project=project,
            task=task,
            inspection=inspection,
            kind=CheckpointKind.MANUAL,
            operator_note=note_exact + "y",
        )

    # Decision at limit passes
    decision_exact = "d" * MAX_DECISION_CHARS
    cp_dec_ok = CheckpointBuilder.build(
        project=project,
        task=task,
        inspection=inspection,
        kind=CheckpointKind.MANUAL,
        decisions=[decision_exact],
    )
    assert cp_dec_ok.payload.decisions == [decision_exact]

    # Decision over limit fails
    with pytest.raises(InvalidCheckpointInputError, match="Decision entry exceeds"):
        CheckpointBuilder.build(
            project=project,
            task=task,
            inspection=inspection,
            kind=CheckpointKind.MANUAL,
            decisions=[decision_exact + "z"],
        )

    # Test summary at limit passes
    summary_exact = "s" * MAX_TEST_SUMMARY_CHARS
    cp_sum_ok = CheckpointBuilder.build(
        project=project,
        task=task,
        inspection=inspection,
        kind=CheckpointKind.MANUAL,
        test_summary=summary_exact,
    )
    assert cp_sum_ok.payload.test_status.summary == summary_exact

    # Test summary over limit fails
    with pytest.raises(InvalidCheckpointInputError, match="Test summary exceeds"):
        CheckpointBuilder.build(
            project=project,
            task=task,
            inspection=inspection,
            kind=CheckpointKind.MANUAL,
            test_summary=summary_exact + "w",
        )
