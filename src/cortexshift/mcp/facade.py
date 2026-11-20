"""Application facade interfacing existing domain and application services for MCP."""

from typing import Any

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.checkpoint_service import save_managed_checkpoint
from cortexshift.application.mcp_invocation import require_active_invocation
from cortexshift.application.repository_service import RepositoryService
from cortexshift.application.task_service import TaskService
from cortexshift.application.workspace import open_workspace
from cortexshift.domain.checkpoint import (
    CHECKPOINT_TRIGGER_DECISION,
    CHECKPOINT_TRIGGER_KEY,
    CheckpointRecord,
    CheckpointTestProvenance,
)
from cortexshift.domain.errors import (
    McpReadOnlyError,
    NoActiveTaskError,
    ProjectConflictError,
)
from cortexshift.domain.session import Session
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
        self._workspace, _ = open_workspace(
            context.workspace or context.project_root, exact_root=True, store_override=store
        )
        if self._workspace.project.id != context.project_id:
            raise ProjectConflictError("Workspace identity mismatch in the MCP project binding.")
        self._store = store
        self._tasks = TaskService(store)
        self._repo_service = repository_service or RepositoryService(
            inspector=GitRepositoryInspector(),
            store=store,
        )

    def _ensure_can_mutate(self) -> None:
        if not self._context.can_mutate():
            raise McpReadOnlyError(
                "State mutation is not permitted in read-only or unmanaged MCP mode."
            )
        self._workspace.revalidate()

    def _require_active_invocation(self) -> tuple[Session, Task]:
        return require_active_invocation(
            self._workspace,
            self._store,
            session_id=self._context.session_id,
            task_id=self._context.task_id,
            provider_id=self._context.provider_id,
            token=self._context.invocation_token,
        )

    def _authorize_task_update(self) -> None:
        self._require_active_invocation()

    def _bound_task_id(self) -> str:
        self._workspace.revalidate()
        if not self._context.task_id:
            raise NoActiveTaskError("No bound task found for this MCP session.")
        return self._context.task_id

    def _get_bound_task(self) -> Task:
        return self._tasks.get_task(self._bound_task_id())

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
        inspection = self._repo_service.inspect_repository(self._workspace)
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
        inspection = self._repo_service.inspect_repository(self._workspace)
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

        updated = self._tasks.update_task(
            self._context.project_id,
            self._bound_task_id(),
            current_work=current_work,
            clear_current_work=current_work is None,
            authorize=self._authorize_task_update,
        )
        return self._task_mutation_result(updated)

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

        updated = self._tasks.update_task(
            self._context.project_id,
            self._bound_task_id(),
            complete_items=cleaned,
            authorize=self._authorize_task_update,
        )
        return self._task_mutation_result(updated)

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

        updated = self._tasks.update_task(
            self._context.project_id,
            self._bound_task_id(),
            add_remaining=cleaned,
            authorize=self._authorize_task_update,
        )
        return self._task_mutation_result(updated)

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

        updated = self._tasks.update_task(
            self._context.project_id,
            self._bound_task_id(),
            add_issues=cleaned,
            authorize=self._authorize_task_update,
        )
        return self._task_mutation_result(updated)

    @staticmethod
    def _task_mutation_result(task: Task) -> TaskMutationResult:
        return TaskMutationResult(
            task_id=task.id,
            updated_at=task.updated_at.isoformat(),
            completed=list(task.completed),
            remaining=list(task.remaining),
            current_work=task.current_work,
            known_issues=list(task.known_issues),
        )

    def record_decision(self, decision: str) -> DecisionResult:
        """Record an architectural decision via a cooperative checkpoint.

        Architectural decisions are persisted in CheckpointRecord.payload.decisions,
        maintaining Task model purity. The checkpoint is immutable and is never rewritten
        by later checkpoints; decisions survive provider handoffs because HandoffBuilder
        aggregates them across the task's whole checkpoint history at handoff time.

        The record carries a structured `trigger="decision"` marker in its existing
        metadata mapping, per ADR-0009. That marker is provenance only: aggregation reads
        every checkpoint's decisions regardless of trigger, and never parses operator_note.
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
            metadata={CHECKPOINT_TRIGGER_KEY: CHECKPOINT_TRIGGER_DECISION},
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
        metadata: dict[str, Any] | None = None,
    ) -> CheckpointRecord:
        """Inspect Git outside the short authorization and persistence transaction."""
        inspection = self._repo_service.inspect_repository(self._workspace)
        return save_managed_checkpoint(
            self._store,
            self._workspace,
            inspection,
            self._require_active_invocation,
            decisions=decisions,
            test_summary=test_summary,
            test_provenance=test_provenance,
            note=note,
            metadata=metadata,
        )
