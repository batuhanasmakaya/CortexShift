"""Application facade interfacing existing domain and application services for MCP."""

from typing import Any

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.checkpoint_builder import CheckpointBuilder
from cortexshift.application.repository_service import RepositoryService
from cortexshift.domain.checkpoint import (
    CheckpointKind,
    CheckpointRecord,
    CheckpointTestProvenance,
)
from cortexshift.domain.errors import (
    McpReadOnlyError,
    NoActiveTaskError,
    TaskNotFoundError,
)
from cortexshift.domain.git import RepositoryInspectionStatus
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.task import Task
from cortexshift.mcp.context import McpExecutionContext
from cortexshift.mcp.models import (
    MAX_CHANGED_PATHS_BUDGET,
    MAX_CURRENT_WORK_CHARS,
    MAX_DECISION_CHARS,
    MAX_ITEM_CHARS,
    MAX_ITEMS_PER_CALL,
    MAX_NOTE_CHARS,
    MAX_TEST_SUMMARY_CHARS,
    CheckpointResult,
    CheckpointSummary,
    CreateCheckpointResult,
    DecisionResult,
    ProjectContextResult,
    ProjectSummary,
    RepositoryStatusSummary,
    SessionSummary,
    TaskMutationResult,
    TaskSummary,
)


class McpApplicationFacade:
    """Coordinates read and write operations requested through MCP.

    Guarantees:
    - Never executes direct SQL outside SQLiteStateStore.
    - Operates strictly on the session-bound Task.
    - Bypasses the exclusive workspace lease (.cortexshift/agent.lock) for state reporting.
    - Enforces read-only mode for unmanaged invocations.
    """

    def __init__(
        self,
        context: McpExecutionContext,
        store: SQLiteStateStore,
        repository_service: RepositoryService | None = None,
    ) -> None:
        self._context = context
        self._store = store
        self._repo_service = repository_service or RepositoryService(
            inspector=GitRepositoryInspector(),
            store=store,
        )

    def _ensure_can_mutate(self) -> None:
        if not self._context.can_mutate():
            raise McpReadOnlyError(
                "State mutation is not permitted in read-only or unmanaged MCP mode."
            )

    def _get_bound_task(self) -> Task:
        if not self._context.task_id:
            raise NoActiveTaskError("No bound task found for this MCP session.")
        task = self._store.get_task(self._context.task_id)
        if task is None:
            raise TaskNotFoundError(self._context.task_id)
        return task

    # -------------------------------------------------------------------------
    # Read Operations
    # -------------------------------------------------------------------------

    def get_project_context(self) -> ProjectContextResult:
        """Return structured, bounded context for the bound project and task."""
        project = self._store.get_project(self._context.project_id)
        proj_name = project.name if project else self._context.project_root.name
        active_tid = self._store.get_active_task_id(self._context.project_id)

        proj_summary = ProjectSummary(
            id=self._context.project_id,
            name=proj_name,
            root=str(self._context.project_root),
            active_task_id=active_tid,
        )

        task_summary: TaskSummary | None = None
        if self._context.task_id:
            task = self._store.get_task(self._context.task_id)
            if task:
                task_summary = TaskSummary(
                    id=task.id,
                    title=task.title,
                    status=task.status.value,
                    objective=task.objective,
                    current_work=task.current_work,
                    completed_count=len(task.completed),
                    remaining_count=len(task.remaining),
                    known_issues_count=len(task.known_issues),
                )

        session_summary: SessionSummary | None = None
        if self._context.session_id:
            session = self._store.get_session(self._context.session_id)
            if session:
                session_summary = SessionSummary(
                    id=session.id,
                    provider_id=str(session.provider_id),
                    status=session.status.value,
                    started_at=session.started_at.isoformat(),
                    native_session_id=session.native_session_id,
                )

        checkpoint_summary: CheckpointSummary | None = None
        if self._context.task_id:
            cp = self._store.get_latest_checkpoint(self._context.task_id)
            if cp:
                test_summary = (
                    cp.payload.test_status.summary if cp.payload.test_status.known else None
                )
                test_prov = (
                    cp.payload.test_status.provenance.value
                    if cp.payload.test_status.known
                    else None
                )
                checkpoint_summary = CheckpointSummary(
                    id=cp.id,
                    kind=cp.kind.value,
                    created_at=cp.created_at.isoformat(),
                    decisions=list(cp.payload.decisions),
                    test_summary=test_summary,
                    test_provenance=test_prov,
                    note=cp.payload.operator_note,
                )

        # Live repository inspection (bounded paths)
        repo_summary: RepositoryStatusSummary | None = None
        inspection = self._repo_service.inspect_repository(self._context.project_root)
        if inspection.snapshot:
            snap = inspection.snapshot
            all_changed = [
                *snap.staged_files,
                *snap.modified_files,
                *snap.untracked_files,
                *snap.conflicted_files,
            ]
            # Deduplicate changed paths
            seen: set[str] = set()
            dedup_changed: list[str] = []
            for p in all_changed:
                if p not in seen:
                    seen.add(p)
                    dedup_changed.append(p)

            budgeted_paths = dedup_changed[:MAX_CHANGED_PATHS_BUDGET]
            omitted = max(0, len(dedup_changed) - MAX_CHANGED_PATHS_BUDGET)

            repo_summary = RepositoryStatusSummary(
                branch=snap.branch,
                head_commit=snap.head_sha,
                dirty=snap.dirty,
                staged_count=len(snap.staged_files),
                modified_count=len(snap.modified_files),
                untracked_count=len(snap.untracked_files),
                conflicted_count=len(snap.conflicted_files),
                diff_shortstat=snap.working_tree_diff_summary or snap.staged_diff_summary,
                changed_paths=budgeted_paths,
                omitted_paths_count=omitted,
            )

        return ProjectContextResult(
            project=proj_summary,
            task=task_summary,
            session=session_summary,
            latest_checkpoint=checkpoint_summary,
            repository=repo_summary,
        )

    def get_current_task(self) -> dict[str, Any]:
        """Return the complete canonical bound Task."""
        task = self._get_bound_task()
        return task.model_dump(mode="json")

    def get_latest_checkpoint(self) -> CheckpointResult:
        """Return the latest checkpoint for the bound task, or null."""
        if not self._context.task_id:
            return CheckpointResult(checkpoint=None)
        cp = self._store.get_latest_checkpoint(self._context.task_id)
        if cp is None:
            return CheckpointResult(checkpoint=None)
        return CheckpointResult(checkpoint=cp.model_dump(mode="json"))

    def get_repository_status(self) -> dict[str, Any]:
        """Perform live, read-only repository inspection."""
        inspection = self._repo_service.inspect_repository(self._context.project_root)
        if inspection.snapshot is None:
            return {
                "available": False,
                "status": inspection.status.value,
                "diagnostic": inspection.diagnostic,
            }
        snap = inspection.snapshot
        return {
            "available": True,
            "status": inspection.status.value,
            "branch": snap.branch,
            "head_sha": snap.head_sha,
            "dirty": snap.dirty,
            "staged": list(snap.staged_files),
            "modified": list(snap.modified_files),
            "untracked": list(snap.untracked_files),
            "conflicted": list(snap.conflicted_files),
            "diff_shortstat": snap.working_tree_diff_summary or snap.staged_diff_summary,
        }

    # -------------------------------------------------------------------------
    # Write Operations
    # -------------------------------------------------------------------------

    def set_current_work(self, current_work: str | None) -> TaskMutationResult:
        """Update Task.current_work atomically."""
        self._ensure_can_mutate()
        if current_work is not None and len(current_work) > MAX_CURRENT_WORK_CHARS:
            raise ValueError(
                f"current_work exceeds maximum length of {MAX_CURRENT_WORK_CHARS} characters."
            )

        clean_work: str | None = (
            current_work.strip() if current_work and current_work.strip() else None
        )
        task = self._get_bound_task()
        updated = task.model_copy(
            update={
                "current_work": clean_work,
                "updated_at": utc_now(),
            }
        )
        self._store.save_task(updated)

        return TaskMutationResult(
            task_id=updated.id,
            updated_at=updated.updated_at.isoformat(),
            completed=list(updated.completed),
            remaining=list(updated.remaining),
            current_work=updated.current_work,
            known_issues=list(updated.known_issues),
        )

    def mark_completed(self, items: list[str]) -> TaskMutationResult:
        """Add items to completed and remove matching entries from remaining."""
        self._ensure_can_mutate()
        if not items:
            raise ValueError("items cannot be empty.")
        if len(items) > MAX_ITEMS_PER_CALL:
            raise ValueError(f"Too many items (maximum {MAX_ITEMS_PER_CALL}).")

        cleaned: list[str] = []
        for it in items:
            s = it.strip()
            if not s:
                continue
            if len(s) > MAX_ITEM_CHARS:
                raise ValueError(f"Item exceeds maximum length of {MAX_ITEM_CHARS} characters.")
            cleaned.append(s)

        if not cleaned:
            raise ValueError("No non-empty items provided.")

        task = self._get_bound_task()

        new_completed = list(task.completed)
        for it in cleaned:
            if it not in new_completed:
                new_completed.append(it)

        new_remaining = [r for r in task.remaining if r not in cleaned]

        updated = task.model_copy(
            update={
                "completed_items": new_completed,
                "remaining_items": new_remaining,
                "updated_at": utc_now(),
            }
        )
        self._store.save_task(updated)

        return TaskMutationResult(
            task_id=updated.id,
            updated_at=updated.updated_at.isoformat(),
            completed=list(updated.completed),
            remaining=list(updated.remaining),
            current_work=updated.current_work,
            known_issues=list(updated.known_issues),
        )

    def add_remaining(self, items: list[str]) -> TaskMutationResult:
        """Append deduplicated items to Task.remaining."""
        self._ensure_can_mutate()
        if not items:
            raise ValueError("items cannot be empty.")
        if len(items) > MAX_ITEMS_PER_CALL:
            raise ValueError(f"Too many items (maximum {MAX_ITEMS_PER_CALL}).")

        cleaned: list[str] = []
        for it in items:
            s = it.strip()
            if not s:
                continue
            if len(s) > MAX_ITEM_CHARS:
                raise ValueError(f"Item exceeds maximum length of {MAX_ITEM_CHARS} characters.")
            cleaned.append(s)

        if not cleaned:
            raise ValueError("No non-empty items provided.")

        task = self._get_bound_task()

        new_remaining = list(task.remaining)
        for it in cleaned:
            if it not in new_remaining:
                new_remaining.append(it)

        updated = task.model_copy(
            update={
                "remaining_items": new_remaining,
                "updated_at": utc_now(),
            }
        )
        self._store.save_task(updated)

        return TaskMutationResult(
            task_id=updated.id,
            updated_at=updated.updated_at.isoformat(),
            completed=list(updated.completed),
            remaining=list(updated.remaining),
            current_work=updated.current_work,
            known_issues=list(updated.known_issues),
        )

    def record_issue(self, items: list[str] | str) -> TaskMutationResult:
        """Append deduplicated issues to Task.known_issues."""
        self._ensure_can_mutate()
        raw_items = [items] if isinstance(items, str) else items
        if not raw_items:
            raise ValueError("items cannot be empty.")
        if len(raw_items) > MAX_ITEMS_PER_CALL:
            raise ValueError(f"Too many items (maximum {MAX_ITEMS_PER_CALL}).")

        cleaned: list[str] = []
        for it in raw_items:
            s = it.strip()
            if not s:
                continue
            if len(s) > MAX_ITEM_CHARS:
                raise ValueError(f"Issue exceeds maximum length of {MAX_ITEM_CHARS} characters.")
            cleaned.append(s)

        if not cleaned:
            raise ValueError("No non-empty issues provided.")

        task = self._get_bound_task()

        new_issues = list(task.known_issues)
        for it in cleaned:
            if it not in new_issues:
                new_issues.append(it)

        updated = task.model_copy(
            update={
                "known_issues": new_issues,
                "updated_at": utc_now(),
            }
        )
        self._store.save_task(updated)

        return TaskMutationResult(
            task_id=updated.id,
            updated_at=updated.updated_at.isoformat(),
            completed=list(updated.completed),
            remaining=list(updated.remaining),
            current_work=updated.current_work,
            known_issues=list(updated.known_issues),
        )

    def record_decision(self, decision: str) -> DecisionResult:
        """Record an architectural decision via a cooperative checkpoint.

        Architectural decisions are persisted in CheckpointRecord.payload.decisions,
        maintaining Task model purity while ensuring decisions survive provider handoffs.
        """
        self._ensure_can_mutate()
        cleaned = decision.strip()
        if not cleaned:
            raise ValueError("Decision cannot be empty or whitespace.")
        if len(cleaned) > MAX_DECISION_CHARS:
            raise ValueError(f"Decision exceeds maximum length of {MAX_DECISION_CHARS} characters.")

        cp = self._create_checkpoint_internal(
            decisions=[cleaned],
            note="Recorded architectural decision via MCP",
        )

        return DecisionResult(
            checkpoint_id=cp.id,
            decision=cleaned,
            created_at=cp.created_at.isoformat(),
        )

    def create_checkpoint(
        self,
        decisions: list[str] | None = None,
        test_summary: str | None = None,
        note: str | None = None,
    ) -> CreateCheckpointResult:
        """Capture a cooperative checkpoint with live repository snapshot and agent reports."""
        self._ensure_can_mutate()

        decisions_list: list[str] = []
        if decisions:
            if len(decisions) > MAX_ITEMS_PER_CALL:
                raise ValueError(
                    f"Decisions list exceeds maximum capacity ({MAX_ITEMS_PER_CALL} items)."
                )
            for d in decisions:
                cd = d.strip()
                if not cd:
                    continue
                if len(cd) > MAX_DECISION_CHARS:
                    raise ValueError(
                        f"Decision exceeds maximum length of {MAX_DECISION_CHARS} characters."
                    )
                decisions_list.append(cd)

        if test_summary and len(test_summary) > MAX_TEST_SUMMARY_CHARS:
            raise ValueError(
                f"test_summary exceeds maximum length of {MAX_TEST_SUMMARY_CHARS} characters."
            )
        if note and len(note) > MAX_NOTE_CHARS:
            raise ValueError(f"note exceeds maximum length of {MAX_NOTE_CHARS} characters.")

        provenance = (
            CheckpointTestProvenance.REPORTED if test_summary else CheckpointTestProvenance.UNKNOWN
        )

        cp = self._create_checkpoint_internal(
            decisions=decisions_list,
            test_summary=test_summary,
            test_provenance=provenance,
            note=note,
        )

        test_stat = cp.payload.test_status
        return CreateCheckpointResult(
            checkpoint_id=cp.id,
            task_id=cp.task_id,
            kind=cp.kind.value,
            created_at=cp.created_at.isoformat(),
            decisions=list(cp.payload.decisions),
            test_summary=test_stat.summary if test_stat.known else None,
            test_provenance=test_stat.provenance.value if test_stat.known else None,
            note=cp.payload.operator_note,
        )

    def _create_checkpoint_internal(
        self,
        decisions: list[str] | None = None,
        test_summary: str | None = None,
        test_provenance: CheckpointTestProvenance = CheckpointTestProvenance.UNKNOWN,
        note: str | None = None,
    ) -> CheckpointRecord:
        """Internal helper to construct and persist a checkpoint record without locking."""
        project = self._store.get_project(self._context.project_id)
        if project is None:
            raise NoActiveTaskError("Project record missing.")

        task = self._get_bound_task()
        session = (
            self._store.get_session(self._context.session_id) if self._context.session_id else None
        )

        # Live Git inspection
        inspection = self._repo_service.inspect_repository(self._context.project_root)
        snapshot_id: str | None = None
        if (
            inspection.status == RepositoryInspectionStatus.READY
            and inspection.snapshot is not None
        ):
            self._store.save_snapshot(inspection.snapshot)
            snapshot_id = inspection.snapshot.id

        record = CheckpointBuilder.build(
            project=project,
            task=task,
            inspection=inspection,
            kind=CheckpointKind.MANUAL,
            snapshot_id=snapshot_id,
            session=session,
            decisions=decisions,
            test_summary=test_summary,
            test_provenance=test_provenance,
            operator_note=note,
        )

        self._store.save_checkpoint(record)
        return record
