"""Deterministic construction of canonical handoff payloads from durable local state.

The builder is pure: it performs no I/O, invokes no provider, and consumes no model
quota. Its only inputs are the canonical Project, the canonical Task, previous
CortexShift Session metadata, a live repository inspection, and the identifier of the
Git snapshot persisted at handoff time.

This is the mechanism that makes CortexShift work after the outgoing agent is already
gone: nothing here requires the outgoing provider to be installed, running, or able to
answer a question.
"""

from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.handoff import (
    HANDOFF_PROTOCOL_VERSION,
    MAX_OPERATOR_NOTE_CHARS,
    HandoffGitState,
    HandoffPayload,
    HandoffSourceSession,
    HandoffTestStatus,
)
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.project import Project
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task

# CortexShift has no durable, verified record of these yet. Phase 5 encodes the absence
# honestly rather than inferring facts it cannot support.
UNKNOWN_DECISIONS_STATEMENT = "No structured decisions are recorded in CortexShift state."
UNKNOWN_TEST_STATUS_STATEMENT = (
    "No verified test result is recorded in CortexShift state. "
    "The receiving agent must run relevant tests before relying on previous claims."
)

_GIT_NOTE_READY = (
    "Live Git inspection succeeded at handoff time. "
    "This is a historical observation, not current truth; re-check with `git status`."
)
_GIT_NOTE_NOT_INSTALLED = (
    "Git was not found on this machine at handoff time. No repository state could be observed."
)
_GIT_NOTE_NOT_REPOSITORY = (
    "This CortexShift project is not inside a Git repository. "
    "No repository state could be observed."
)
_GIT_NOTE_PROBE_ERROR = (
    "Git repository inspection failed at handoff time. "
    "Treat any repository claim below as unverified and inspect the workspace directly."
)

_GIT_NOTES: dict[RepositoryInspectionStatus, str] = {
    RepositoryInspectionStatus.READY: _GIT_NOTE_READY,
    RepositoryInspectionStatus.GIT_NOT_INSTALLED: _GIT_NOTE_NOT_INSTALLED,
    RepositoryInspectionStatus.NOT_GIT_REPOSITORY: _GIT_NOTE_NOT_REPOSITORY,
    RepositoryInspectionStatus.PROBE_ERROR: _GIT_NOTE_PROBE_ERROR,
}


def derive_files_touched(inspection: RepositoryInspection) -> list[str]:
    """Derive the FILES TOUCHED list from live Git inspection.

    Deduplicates the union of staged, modified, untracked, and conflicted paths while
    preserving a deterministic order. Source files are never opened to populate this
    field, and full diffs are never read or stored.
    """
    snapshot = inspection.snapshot
    if snapshot is None:
        return []

    ordered: list[str] = []
    seen: set[str] = set()
    for group in (
        snapshot.staged_files,
        snapshot.modified_files,
        snapshot.untracked_files,
        snapshot.conflicted_files,
    ):
        for path in group:
            if path not in seen:
                seen.add(path)
                ordered.append(path)
    return ordered


def build_git_state(
    inspection: RepositoryInspection,
    snapshot_id: str | None = None,
) -> HandoffGitState:
    """Build the canonical GIT STATE section, honestly encoding unavailable Git."""
    note = _GIT_NOTES.get(inspection.status, _GIT_NOTE_PROBE_ERROR)
    snapshot = inspection.snapshot

    if inspection.status != RepositoryInspectionStatus.READY or snapshot is None:
        return HandoffGitState(status=inspection.status, available=False, note=note)

    return HandoffGitState(
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


def derive_recommended_next_action(task: Task) -> str:
    """Derive the RECOMMENDED NEXT ACTION deterministically from canonical task state.

    Priority: current work, then the first remaining item, then repository inspection.
    No model call is ever used to produce this recommendation.
    """
    current = (task.current_work or "").strip()
    if current:
        return f"Continue the work already in progress: {current}"

    for item in task.remaining_items:
        candidate = item.strip()
        if candidate:
            return f"Start the next recorded remaining item: {candidate}"

    return (
        "Inspect the current repository state (git status, changed files, and the "
        "project's relevant tests) and determine the next incomplete step toward the "
        "original objective."
    )


def bound_operator_note(note: str | None) -> str | None:
    """Bound an optional operator note so it can never make the handoff unbounded."""
    if note is None:
        return None
    cleaned = note.strip()
    if not cleaned:
        return None
    if len(cleaned) > MAX_OPERATOR_NOTE_CHARS:
        omitted = len(cleaned) - MAX_OPERATOR_NOTE_CHARS
        return f"{cleaned[:MAX_OPERATOR_NOTE_CHARS]}… [{omitted} characters omitted]"
    return cleaned


class HandoffBuilder:
    """Builds canonical handoff payloads deterministically from durable state."""

    def build(
        self,
        project: Project,
        task: Task,
        source_session: Session,
        inspection: RepositoryInspection,
        target_provider_id: ProviderId,
        snapshot_id: str | None = None,
        operator_note: str | None = None,
    ) -> HandoffPayload:
        """Build the canonical handoff payload for a task moving to another provider."""
        return HandoffPayload(
            protocol_version=HANDOFF_PROTOCOL_VERSION,
            generated_at=utc_now(),
            project_name=project.name,
            project_root=project.repo_path,
            task_id=task.id,
            task_title=task.title,
            task_status=task.status.value,
            original_objective=task.objective,
            requirements=list(task.requirements),
            constraints=list(task.constraints),
            completed=list(task.completed_items),
            current_work=task.current_work,
            remaining=list(task.remaining_items),
            # CortexShift records no structured decisions yet; the absence stays explicit.
            important_decisions=[],
            decisions_known=False,
            files_touched=derive_files_touched(inspection),
            test_status=HandoffTestStatus(known=False, summary=UNKNOWN_TEST_STATUS_STATEMENT),
            known_issues=list(task.known_issues),
            git_state=build_git_state(inspection, snapshot_id=snapshot_id),
            # Completed work is the only signal CortexShift durably holds about what
            # should not be rebuilt. It remains advisory and must still be verified.
            do_not_redo=list(task.completed_items),
            recommended_next_action=derive_recommended_next_action(task),
            source_session=HandoffSourceSession(
                session_id=source_session.id,
                provider_id=source_session.provider_id,
                status=source_session.status,
                started_at=source_session.started_at,
                ended_at=source_session.ended_at,
                exit_reason=source_session.exit_reason,
                exit_code=source_session.exit_code,
            ),
            target_provider_id=target_provider_id,
            operator_note=bound_operator_note(operator_note),
        )
