"""Deterministic construction of canonical checkpoint records and payloads.

The builder is pure: it performs no I/O, invokes no provider, and consumes no model
quota. Its inputs are canonical Project, canonical Task, live repository inspection,
optional Session metadata, and optional enrichment (decisions, test summary, note).
"""

from typing import Any

from cortexshift.application.handoff_builder import derive_files_touched
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
    generate_checkpoint_id,
)
from cortexshift.domain.errors import InvalidCheckpointInputError
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.project import Project
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task, TaskStatus

_GIT_NOTE_READY = (
    "Live Git inspection succeeded at checkpoint time. "
    "This is a historical observation, not current truth; re-check with `git status`."
)
_GIT_NOTE_NOT_INSTALLED = (
    "Git was not found on this machine at checkpoint time. No repository state could be observed."
)
_GIT_NOTE_NOT_REPOSITORY = (
    "This CortexShift project is not inside a Git repository. "
    "No repository state could be observed."
)
_GIT_NOTE_PROBE_ERROR = (
    "Git repository inspection failed at checkpoint time. "
    "Treat any repository claim below as unverified and inspect the workspace directly."
)

_GIT_NOTES: dict[RepositoryInspectionStatus, str] = {
    RepositoryInspectionStatus.READY: _GIT_NOTE_READY,
    RepositoryInspectionStatus.GIT_NOT_INSTALLED: _GIT_NOTE_NOT_INSTALLED,
    RepositoryInspectionStatus.NOT_GIT_REPOSITORY: _GIT_NOTE_NOT_REPOSITORY,
    RepositoryInspectionStatus.PROBE_ERROR: _GIT_NOTE_PROBE_ERROR,
}


def build_checkpoint_git_state(
    inspection: RepositoryInspection,
    snapshot_id: str | None = None,
) -> CheckpointGitState:
    """Build the checkpoint GIT STATE section honestly encoding unavailable Git."""
    note = _GIT_NOTES.get(inspection.status, _GIT_NOTE_PROBE_ERROR)
    snapshot = inspection.snapshot

    if inspection.status != RepositoryInspectionStatus.READY or snapshot is None:
        return CheckpointGitState(status=inspection.status, available=False, note=note)

    return CheckpointGitState(
        status=inspection.status,
        available=True,
        note=note,
        branch=snapshot.branch,
        head_sha=snapshot.head_sha,
        detached_head=snapshot.detached_head,
        dirty=snapshot.dirty,
        staged_count=len(snapshot.staged_files),
        modified_count=len(snapshot.modified_files),
        untracked_count=len(snapshot.untracked_files),
        conflicted_count=len(snapshot.conflicted_files),
        working_tree_diff_summary=snapshot.working_tree_diff_summary,
        staged_diff_summary=snapshot.staged_diff_summary,
        snapshot_id=snapshot_id,
    )


class CheckpointBuilder:
    """Constructs canonical CheckpointRecord and CheckpointPayload entities deterministically."""

    @classmethod
    def build(
        cls,
        project: Project,
        task: Task,
        inspection: RepositoryInspection,
        kind: CheckpointKind,
        snapshot_id: str | None = None,
        session: Session | None = None,
        decisions: list[str] | None = None,
        test_summary: str | None = None,
        test_provenance: CheckpointTestProvenance = CheckpointTestProvenance.UNKNOWN,
        operator_note: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointRecord:
        """Build a canonical CheckpointRecord from durable inputs.

        Raises:
            InvalidCheckpointInputError: If note, decisions, or test summary exceed bounds.
        """
        # Validate inputs
        cleaned_note: str | None = None
        if operator_note is not None:
            cleaned_note = operator_note.strip()
            if len(cleaned_note) > MAX_OPERATOR_NOTE_CHARS:
                raise InvalidCheckpointInputError(
                    f"Operator note exceeds maximum allowed length of "
                    f"{MAX_OPERATOR_NOTE_CHARS} characters."
                )
            if not cleaned_note:
                cleaned_note = None

        cleaned_decisions: list[str] = []
        if decisions is not None:
            for decision in decisions:
                d = decision.strip()
                if d:
                    if len(d) > MAX_DECISION_CHARS:
                        raise InvalidCheckpointInputError(
                            f"Decision entry exceeds maximum allowed length of "
                            f"{MAX_DECISION_CHARS} characters."
                        )
                    cleaned_decisions.append(d)

        cleaned_test_summary: str | None = None
        if test_summary is not None:
            cleaned_test_summary = test_summary.strip()
            if len(cleaned_test_summary) > MAX_TEST_SUMMARY_CHARS:
                raise InvalidCheckpointInputError(
                    f"Test summary exceeds maximum allowed length of "
                    f"{MAX_TEST_SUMMARY_CHARS} characters."
                )
            if not cleaned_test_summary:
                cleaned_test_summary = None

        # Build test status
        if cleaned_test_summary is not None:
            provenance = (
                test_provenance
                if test_provenance != CheckpointTestProvenance.UNKNOWN
                else CheckpointTestProvenance.REPORTED
            )
            test_status = CheckpointTestStatus(
                known=True,
                summary=cleaned_test_summary,
                provenance=provenance,
            )
        else:
            test_status = CheckpointTestStatus(
                known=False,
                summary="No independently verified test results recorded.",
                provenance=CheckpointTestProvenance.UNKNOWN,
            )

        # Build task snapshot
        status_val = task.status.value if isinstance(task.status, TaskStatus) else str(task.status)
        task_snapshot = CheckpointTaskSnapshot(
            task_id=task.id,
            task_title=task.title,
            task_status=status_val,
            objective=task.objective,
            requirements=list(task.requirements),
            constraints=list(task.constraints),
            completed=list(task.completed_items),
            current_work=task.current_work,
            remaining=list(task.remaining_items),
            known_issues=list(task.known_issues),
        )

        # Build source session summary
        source_session_summary: CheckpointSourceSession | None = None
        if session is not None:
            source_session_summary = CheckpointSourceSession(
                session_id=session.id,
                provider_id=session.provider_id,
                native_session_id=session.native_session_id,
                status=session.status,
                exit_reason=session.exit_reason,
                exit_code=session.exit_code,
            )

        # Build Git state & files touched
        git_state = build_checkpoint_git_state(inspection, snapshot_id=snapshot_id)
        files_touched = derive_files_touched(inspection)

        payload = CheckpointPayload(
            protocol_version=CHECKPOINT_PROTOCOL_VERSION,
            generated_at=utc_now(),
            task=task_snapshot,
            git_state=git_state,
            files_touched=files_touched,
            decisions=cleaned_decisions,
            test_status=test_status,
            operator_note=cleaned_note,
            source_session=source_session_summary,
            metadata=dict(metadata or {}),
        )

        return CheckpointRecord(
            id=generate_checkpoint_id(),
            protocol_version=CHECKPOINT_PROTOCOL_VERSION,
            project_id=project.id,
            task_id=task.id,
            session_id=session.id if session else None,
            git_snapshot_id=snapshot_id,
            kind=kind,
            payload=payload,
            created_at=utc_now(),
            metadata=dict(metadata or {}),
        )
