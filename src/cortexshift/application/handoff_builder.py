"""Deterministic construction of canonical handoff payloads from durable local state.

The builder is pure: it performs no I/O, invokes no provider, and consumes no model
quota. Its only inputs are the canonical Project, the canonical Task, previous
CortexShift Session metadata, a live repository inspection, and the identifier of the
Git snapshot persisted at handoff time.

This is the mechanism that makes CortexShift work after the outgoing agent is already
gone: nothing here requires the outgoing provider to be installed, running, or able to
answer a question.
"""

from collections.abc import Iterable

from cortexshift.domain.checkpoint import CheckpointRecord
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

# Emitted only when the task's entire checkpoint history holds no structured decision.
# CortexShift encodes a genuine absence honestly rather than inferring facts it cannot
# support — and, equally, never claims absence over decisions it actually holds.
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


def aggregate_task_decisions(
    checkpoints: Iterable[CheckpointRecord],
    task_id: str,
) -> list[str]:
    """Aggregate the structured decisions recorded across one task's checkpoint history.

    A checkpoint is an immutable point-in-time observation and is never rewritten, so a
    decision recorded by `record_decision` lives only in the checkpoint that minted it.
    Task-level durability is therefore reconstructed here, at handoff time, by reading the
    task's checkpoint history instead of only its newest checkpoint.

    Semantics:
    - **Chronological first-seen order.** Callers supply checkpoints oldest first; the
      resulting order follows the order in which each decision first entered state.
    - **Exact-equality de-duplication.** A decision string repeated across checkpoints is
      emitted once, at its first occurrence. Text is never normalised, trimmed for
      comparison, or fuzzy-matched — two decisions differing by a single character stay
      distinct.
    - **Task isolation.** Records whose `task_id` does not match are dropped, so a store
      query that ever widened its scope still could not leak another task's decisions.

    This function is pure: it performs no I/O and mutates nothing it is given.
    """
    aggregated: list[str] = []
    seen: set[str] = set()
    for record in checkpoints:
        if record.task_id != task_id:
            continue
        for decision in record.payload.decisions:
            if not decision.strip():
                continue
            if decision in seen:
                continue
            seen.add(decision)
            aggregated.append(decision)
    return aggregated


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
        latest_checkpoint: CheckpointRecord | None = None,
        task_decisions: list[str] | None = None,
    ) -> HandoffPayload:
        """Build the canonical handoff payload for a task moving to another provider.

        `task_decisions` carries the decisions aggregated across the task's whole
        checkpoint history (see `aggregate_task_decisions`) and is authoritative when
        supplied — including when it is empty, which asserts that the task genuinely has
        no recorded decision. `latest_checkpoint` remains the provenance reference for the
        snapshot and reported test status; it is used as the decision source only when no
        aggregate was supplied at all.
        """
        important_decisions: list[str] = []
        decisions_known: bool = False
        if task_decisions is not None:
            important_decisions = list(task_decisions)
            decisions_known = bool(important_decisions)
        elif latest_checkpoint is not None and latest_checkpoint.payload.decisions:
            important_decisions = list(latest_checkpoint.payload.decisions)
            decisions_known = True

        test_status: HandoffTestStatus
        if latest_checkpoint is not None and latest_checkpoint.payload.test_status.known:
            test_status = HandoffTestStatus(
                known=True,
                summary=(
                    f"Checkpoint-reported test status:\n"
                    f"{latest_checkpoint.payload.test_status.summary}\n\n"
                    f"This result was not independently verified by CortexShift. "
                    f"Re-run relevant tests before relying on it."
                ),
            )
        else:
            test_status = HandoffTestStatus(known=False, summary=UNKNOWN_TEST_STATUS_STATEMENT)

        source_checkpoint_id = latest_checkpoint.id if latest_checkpoint else None
        source_checkpoint_kind = latest_checkpoint.kind.value if latest_checkpoint else None
        source_checkpoint_created_at = latest_checkpoint.created_at if latest_checkpoint else None

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
            important_decisions=important_decisions,
            decisions_known=decisions_known,
            files_touched=derive_files_touched(inspection),
            test_status=test_status,
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
            source_checkpoint_id=source_checkpoint_id,
            source_checkpoint_kind=source_checkpoint_kind,
            source_checkpoint_created_at=source_checkpoint_created_at,
        )
