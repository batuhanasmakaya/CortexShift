"""Read-model assembly and use-case coordination for the terminal control center.

`TuiFacade` is the only object the dashboard talks to. It aggregates the existing
application services, projects their results into immutable presentation models, and
holds no business rules of its own: task rules stay in `Task`/`TaskService`, recovery
stays in `RecoveryService`, handoff generation stays in `SwitchService`, provider argv
stays behind the provider adapters.

Architectural boundaries enforced here:

- No SQL, and no direct SQLite access — persistence is reached through application
  services only.
- No Git subprocess invocation — repository truth comes from `RepositoryService`.
- Every call is bound to the project root resolved when the facade was constructed, so
  no dashboard action can reach a different project.
- The dashboard holds no workspace lease. Operations that require exclusivity (recovery,
  run, resume, switch) acquire it inside their own services, which reject unsafe attempts.
"""

import shutil
import time
from collections.abc import Callable
from pathlib import Path

from cortexshift import __version__
from cortexshift.adapters.providers.antigravity import (
    ANTIGRAVITY_MCP_CONFIG_REL_PATH,
    is_antigravity_mcp_configured,
    setup_antigravity_mcp,
)
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.doctor import DoctorService
from cortexshift.application.handoff_service import HandoffService
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.native_session import is_native_resumable, native_capabilities
from cortexshift.application.recovery_service import RecoveryReport, RecoveryService
from cortexshift.application.repository_service import RepositoryService
from cortexshift.application.run_service import ProviderRuntimeRegistry
from cortexshift.application.session_service import SessionService
from cortexshift.application.status_service import ProjectStatusService
from cortexshift.application.switch_service import SwitchService
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.domain.checkpoint import CheckpointKind, CheckpointRecord, CheckpointTestProvenance
from cortexshift.domain.errors import ProjectNotInitializedError
from cortexshift.domain.git import RepositoryInspectionStatus
from cortexshift.domain.handoff import HandoffRecord
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task
from cortexshift.ports.workspace_lease import WorkspaceLeaseManager
from cortexshift.tui.models import (
    TuiActivityModel,
    TuiCheckpointRow,
    TuiHandoffPreview,
    TuiHandoffRow,
    TuiMcpStatus,
    TuiProjectModel,
    TuiProviderStatus,
    TuiRecoveryPreview,
    TuiRepositoryModel,
    TuiSessionRow,
    TuiStateSnapshot,
    TuiSwitchPreview,
    TuiTaskModel,
    TuiTaskRow,
    WorkspaceActivity,
)

DEFAULT_SESSION_LIMIT = 50
DEFAULT_CHECKPOINT_LIMIT = 50
DEFAULT_HANDOFF_LIMIT = 50
PROVIDER_CACHE_SECONDS = 30.0

MCP_READ_TOOLS = (
    "get_project_context",
    "get_current_task",
    "get_latest_checkpoint",
    "get_repository_status",
)
MCP_WRITE_TOOLS = (
    "set_current_work",
    "mark_completed",
    "add_remaining",
    "record_issue",
    "record_decision",
    "create_checkpoint",
)
MCP_RESOURCES = (
    "cortexshift://project",
    "cortexshift://task",
    "cortexshift://checkpoint/latest",
    "cortexshift://repository",
)

MCP_INTEGRATION_MODES = {
    str(PROVIDER_CLAUDE): "automatic per launch (--mcp-config)",
    str(PROVIDER_CODEX): "automatic per launch (-c overrides)",
    str(PROVIDER_ANTIGRAVITY): "workspace config (.agents/mcp_config.json)",
}


class TuiFacade:
    """Assembles dashboard read models and coordinates existing use cases."""

    def __init__(
        self,
        project_root: Path,
        *,
        status_service: ProjectStatusService | None = None,
        task_workspace: TaskWorkspaceService | None = None,
        session_service: SessionService | None = None,
        checkpoint_service: CheckpointService | None = None,
        handoff_service: HandoffService | None = None,
        repository_service: RepositoryService | None = None,
        recovery_service: RecoveryService | None = None,
        doctor_service: DoctorService | None = None,
        switch_service: SwitchService | None = None,
        runtime_registry: ProviderRuntimeRegistry | None = None,
        lease_manager: WorkspaceLeaseManager | None = None,
        which_fn: Callable[[str], str | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
        provider_cache_seconds: float = PROVIDER_CACHE_SECONDS,
    ) -> None:
        self._root = Path(project_root).resolve()
        self._status = status_service or ProjectStatusService()
        self._task_workspace = task_workspace or TaskWorkspaceService()
        self._sessions = session_service or SessionService()
        self._checkpoints = checkpoint_service or CheckpointService()
        self._handoffs = handoff_service or HandoffService()
        self._repository = repository_service or RepositoryService()
        self._recovery = recovery_service or RecoveryService()
        self._doctor = doctor_service or DoctorService()
        self._switch = switch_service or SwitchService()
        self._runtime_registry = runtime_registry or ProviderRuntimeRegistry()
        self._lease_manager = lease_manager or FileWorkspaceLeaseManager()
        self._which = which_fn if which_fn is not None else (lambda cmd: shutil.which(cmd))
        self._clock = clock
        self._provider_cache_seconds = provider_cache_seconds
        self._provider_cache: tuple[float, tuple[TuiProviderStatus, ...]] | None = None

    @property
    def project_root(self) -> Path:
        """The single project root every dashboard action is bound to."""
        return self._root

    @classmethod
    def resolve(
        cls,
        start_dir: Path | str | None = None,
        **kwargs: object,
    ) -> "TuiFacade":
        """Build a facade bound to the nearest initialized project root.

        Raises:
            ProjectNotInitializedError: If no initialized project root is found.
        """
        start_path = Path(start_dir) if start_dir is not None else None
        project_root = ProjectLocator.find_project_root(start_path)
        if project_root is None:
            raise ProjectNotInitializedError()
        return cls(project_root, **kwargs)  # type: ignore[arg-type]

    # ------------------------------------------------------------------
    # Lightweight state (SQLite-backed only; never spawns a subprocess)
    # ------------------------------------------------------------------

    def load_state(self) -> TuiStateSnapshot:
        """Assemble every persisted read model in one pass.

        Deliberately performs no Git inspection and no provider probing so the dashboard's
        lightweight refresh timer stays cheap enough to run continuously.
        """
        status = self._status.get_status(self._root)
        project = TuiProjectModel(
            project_id=status.project_id,
            name=status.name,
            root=self._root,
            state_file=status.state_file,
            schema_version=status.schema_version,
            cortexshift_version=__version__,
        )

        tasks = self._task_workspace.list_tasks(self._root)
        active_task_id = status.active_task.id if status.active_task else None
        active_task = next((t for t in tasks if t.id == active_task_id), None)

        sessions = tuple(
            self._session_row(session)
            for session in self._sessions.list_sessions(self._root, limit=DEFAULT_SESSION_LIMIT)
        )
        checkpoints = tuple(
            self._checkpoint_row(record)
            for record in self._checkpoints.list_checkpoints(
                task_id=active_task_id,
                limit=DEFAULT_CHECKPOINT_LIMIT,
                start_dir=self._root,
            )
        )
        handoffs = tuple(
            self._handoff_row(record)
            for record in self._handoffs.list_handoffs(self._root, limit=DEFAULT_HANDOFF_LIMIT)
        )

        return TuiStateSnapshot(
            project=project,
            active_task=TuiTaskModel.from_task(active_task) if active_task else None,
            tasks=tuple(self._task_row(task, active_task_id) for task in tasks),
            sessions=sessions,
            checkpoints=checkpoints,
            handoffs=handoffs,
            activity=TuiActivityModel(
                latest_session=sessions[0] if sessions else None,
                latest_checkpoint=checkpoints[0] if checkpoints else None,
                latest_handoff=handoffs[0] if handoffs else None,
                unfinalized_session_count=sum(1 for row in sessions if row.unfinalized),
            ),
            loaded_at=utc_now(),
        )

    # ------------------------------------------------------------------
    # Live repository inspection (Worker-only; runs native Git)
    # ------------------------------------------------------------------

    def inspect_repository(self) -> TuiRepositoryModel:
        """Run a live, strictly read-only Git inspection of the bound project."""
        inspection = self._repository.inspect_repository(self._root)
        snapshot = inspection.snapshot
        if snapshot is None:
            return TuiRepositoryModel(
                status=inspection.status,
                project_root=self._root,
                git_available=inspection.git_available,
                git_version=inspection.git_version,
                diagnostic=inspection.diagnostic,
                observed_at=utc_now(),
            )

        return TuiRepositoryModel(
            status=inspection.status,
            project_root=self._root,
            git_available=inspection.git_available,
            git_version=inspection.git_version or snapshot.git_version,
            git_root=snapshot.git_root,
            branch=snapshot.branch,
            head_sha=snapshot.head_sha,
            detached_head=snapshot.detached_head,
            dirty=snapshot.dirty,
            staged_files=tuple(snapshot.staged_files),
            modified_files=tuple(snapshot.modified_files),
            untracked_files=tuple(snapshot.untracked_files),
            conflicted_files=tuple(snapshot.conflicted_files),
            working_tree_diff_summary=snapshot.working_tree_diff_summary,
            staged_diff_summary=snapshot.staged_diff_summary,
            diagnostic=inspection.diagnostic,
            observed_at=snapshot.captured_at,
        )

    # ------------------------------------------------------------------
    # Provider discovery and MCP status (Worker-only; probes native CLIs)
    # ------------------------------------------------------------------

    def provider_status(self, *, refresh: bool = False) -> tuple[TuiProviderStatus, ...]:
        """Passively discover provider CLIs, with a short in-process cache.

        The cache lives only for the lifetime of this dashboard process; nothing is
        persisted. `refresh=True` forces a fresh probe.
        """
        now = self._clock()
        if not refresh and self._provider_cache is not None:
            cached_at, cached = self._provider_cache
            if now - cached_at < self._provider_cache_seconds:
                return cached

        report = self._doctor.run_diagnostics()
        statuses = tuple(
            TuiProviderStatus(
                provider_id=str(diagnostic.provider_id),
                display_name=diagnostic.display_name,
                executable=diagnostic.executable,
                installed=diagnostic.installed,
                version=diagnostic.version,
                authentication=diagnostic.authentication_status.value,
                supports_native_resume=diagnostic.capabilities.supports_native_resume,
                supports_exact_resume=self._supports_exact_resume(str(diagnostic.provider_id)),
                mcp_integration=MCP_INTEGRATION_MODES.get(
                    str(diagnostic.provider_id), "not integrated"
                ),
                diagnostics=tuple(diagnostic.diagnostics),
            )
            for diagnostic in report.providers
        )
        self._provider_cache = (now, statuses)
        return statuses

    def mcp_status(self) -> TuiMcpStatus:
        """Summarize MCP availability and provider integration without starting a server."""
        try:
            import mcp

            sdk_version = str(getattr(mcp, "__version__", "unknown"))
            sdk_available = True
        except Exception:  # pragma: no cover - the SDK is a hard runtime dependency
            sdk_version = "unavailable"
            sdk_available = False

        return TuiMcpStatus(
            sdk_available=sdk_available,
            sdk_version=sdk_version,
            antigravity_configured=is_antigravity_mcp_configured(self._root),
            antigravity_config_path=str(self._root / ANTIGRAVITY_MCP_CONFIG_REL_PATH),
            read_tools=MCP_READ_TOOLS,
            write_tools=MCP_WRITE_TOOLS,
            resources=MCP_RESOURCES,
        )

    def configure_antigravity_mcp(
        self, *, dry_run: bool = False, force: bool = False
    ) -> dict[str, object]:
        """Configure workspace MCP for Antigravity using the same safe setup path as the CLI.

        Preserves unrelated servers and refuses to overwrite a conflicting entry unless
        the operator explicitly forces it.
        """
        return setup_antigravity_mcp(self._root, dry_run=dry_run, force=force)

    # ------------------------------------------------------------------
    # Workspace activity (non-invasive advisory lock probe)
    # ------------------------------------------------------------------

    def workspace_activity(self) -> WorkspaceActivity:
        """Probe whether another agent currently owns the exclusive workspace lease.

        The OS advisory lock is authoritative: presence of the lock file on disk proves
        nothing. When the lease is free the probe acquires and immediately releases it,
        so the dashboard never holds the lease.
        """
        try:
            lease = self._lease_manager.get_lease(self._root)
            if lease.acquire():
                lease.release()
                return WorkspaceActivity.FREE
            return WorkspaceActivity.BUSY
        except Exception:
            return WorkspaceActivity.UNKNOWN

    # ------------------------------------------------------------------
    # Task operations (delegated to canonical services)
    # ------------------------------------------------------------------

    def set_current_work(self, current_work: str | None) -> Task:
        """Set the active Task's in-flight work description."""
        return self._task_workspace.set_current_work(current_work, self._root)

    def mark_completed(self, items: list[str]) -> Task:
        """Mark items completed on the active Task and drop them from remaining."""
        return self._task_workspace.mark_completed(items, self._root)

    def add_remaining(self, items: list[str]) -> Task:
        """Append newly discovered remaining items to the active Task."""
        return self._task_workspace.add_remaining(items, self._root)

    def record_issues(self, items: list[str]) -> Task:
        """Record known issues on the active Task."""
        return self._task_workspace.record_issues(items, self._root)

    def activate_task(self, task_id: str) -> Task:
        """Activate an eligible Task using canonical activation rules."""
        return self._task_workspace.activate_task(task_id, self._root)

    def start_task(
        self,
        title: str,
        objective: str,
        requirements: list[str] | None = None,
        constraints: list[str] | None = None,
    ) -> Task:
        """Create a new Task and make it active."""
        return self._task_workspace.start_task(
            title=title,
            objective=objective,
            requirements=requirements,
            constraints=constraints,
            start_dir=self._root,
        )

    # ------------------------------------------------------------------
    # Checkpoints, recovery, handoff preview
    # ------------------------------------------------------------------

    def create_checkpoint(
        self,
        decisions: list[str] | None = None,
        test_summary: str | None = None,
        note: str | None = None,
    ) -> CheckpointRecord:
        """Capture a cooperative MANUAL checkpoint without acquiring the workspace lease.

        Reported test summaries are recorded with `reported` provenance; CortexShift never
        upgrades an operator's claim into verified evidence.
        """
        provenance = (
            CheckpointTestProvenance.REPORTED if test_summary else CheckpointTestProvenance.UNKNOWN
        )
        return self._checkpoints.create_checkpoint(
            kind=CheckpointKind.MANUAL,
            decisions=decisions,
            test_summary=test_summary,
            test_provenance=provenance,
            note=note,
            start_dir=self._root,
        )

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord:
        """Retrieve one immutable checkpoint by its full canonical identifier."""
        return self._checkpoints.get_checkpoint(checkpoint_id, start_dir=self._root)

    def preview_recovery(self) -> TuiRecoveryPreview:
        """Preview what a recovery run would reconcile, mutating nothing."""
        report = self._recovery.recover(start_dir=self._root, dry_run=True)
        return self._recovery_preview(report)

    def recover(self) -> RecoveryReport:
        """Reconcile unfinalized sessions through the existing recovery service.

        Recovery requires the exclusive workspace lease; if another agent owns the
        workspace the service raises and the dashboard reports it without mutating state.
        """
        return self._recovery.recover(start_dir=self._root, dry_run=False)

    def preview_handoff(self, target_provider: str, note: str | None = None) -> TuiHandoffPreview:
        """Render the canonical handoff for a target provider without side effects.

        Persists no handoff, captures no snapshot, starts no bootstrap turn, and consumes
        zero model quota.
        """
        result = self._switch.preview(
            target_provider_name=target_provider,
            note=note,
            start_dir=self._root,
        )
        return TuiHandoffPreview(
            target_provider_id=result.target_provider_id,
            target_provider_name=result.target_provider_name,
            delivery_strategy=result.delivery_strategy,
            bootstrap_model_turn_required=result.bootstrap_model_turn_required,
            rendered_context=result.rendered_context,
            context_characters=result.context_characters,
            context_max_characters=result.context_max_characters,
            context_truncated=result.context_truncated,
        )

    def preview_switch(
        self,
        target_provider: str,
        *,
        new_session: bool = False,
        resume_session_id: str | None = None,
        note: str | None = None,
    ) -> TuiSwitchPreview:
        """Describe what a switch would do, using the existing switch dry-run path."""
        result = self._switch.dry_run(
            target_provider_name=target_provider,
            note=note,
            start_dir=self._root,
            new_session=new_session,
            resume_session_id=resume_session_id,
        )

        checkpoint_enrichment = "No checkpoint recorded for the active task."
        latest = self._checkpoints.get_latest_checkpoint(start_dir=self._root)
        if latest is not None:
            decisions = len(latest.payload.decisions)
            tests = "reported (unverified)" if latest.payload.test_status.known else "unknown"
            checkpoint_enrichment = (
                f"{latest.kind.value} checkpoint {latest.id} · "
                f"{decisions} decision(s) · tests {tests}"
            )

        return TuiSwitchPreview(
            target_provider_id=result.target_provider_id,
            target_provider_name=result.target_provider_name,
            source_provider_id=result.source_provider_id,
            source_session_id=result.source_session_id,
            task_id=result.task_id,
            task_title=result.task_title,
            target_native_mode=result.target_native_mode,
            selected_prior_target_session_id=result.selected_prior_target_session_id,
            git_status=result.git_status,
            git_branch=result.git_branch,
            git_dirty=result.git_dirty,
            delivery_strategy=result.delivery_strategy,
            bootstrap_model_turn_required=result.bootstrap_model_turn_required,
            checkpoint_enrichment=checkpoint_enrichment,
            context_characters=result.context_characters,
            context_max_characters=result.context_max_characters,
            context_truncated=result.context_truncated,
        )

    # ------------------------------------------------------------------
    # Provider action eligibility
    # ------------------------------------------------------------------

    def supported_providers(self) -> tuple[str, ...]:
        """Canonical provider identifiers the runtime registry can launch."""
        return tuple(self._runtime_registry.list_supported_ids())

    def resumable_sessions(self, provider_id: str) -> tuple[TuiSessionRow, ...]:
        """Sessions that can be exactly resumed for a provider on the active task.

        Selection reuses the canonical `is_native_resumable` rule; the dashboard never
        guesses provider-native identity.
        """
        snapshot_sessions = self._sessions.list_sessions(self._root, limit=DEFAULT_SESSION_LIMIT)
        active = self._task_workspace.get_active_task(self._root)
        if active is None:
            return ()
        adapter = self._runtime_registry.get(provider_id)
        capabilities = native_capabilities(adapter)
        return tuple(
            self._session_row(session)
            for session in snapshot_sessions
            if session.task_id == active.id
            and str(session.provider_id) == provider_id
            and is_native_resumable(session, capabilities)
        )

    def provider_available(self, provider_id: str) -> bool:
        """Whether a provider executable currently resolves on PATH."""
        adapter = self._runtime_registry.get(provider_id)
        if adapter is None:
            return False
        return self._which(adapter.executable) is not None

    # ------------------------------------------------------------------
    # Projection helpers
    # ------------------------------------------------------------------

    def _supports_exact_resume(self, provider_id: str) -> bool:
        adapter = self._runtime_registry.get(provider_id)
        return native_capabilities(adapter).supports_exact_resume

    def _session_row(self, session: Session) -> TuiSessionRow:
        adapter = self._runtime_registry.get(str(session.provider_id))
        resumable = is_native_resumable(session, native_capabilities(adapter))
        return TuiSessionRow(
            id=session.id,
            task_id=session.task_id,
            provider_id=str(session.provider_id),
            status=session.status.value,
            native_resumable=resumable,
            native_session_id=session.native_session_id,
            resumed_from_session_id=session.resumed_from_session_id,
            exit_reason=session.exit_reason.value if session.exit_reason else None,
            exit_code=session.exit_code,
            started_at=session.started_at,
            ended_at=session.ended_at,
            reconciled_at=session.reconciled_at,
        )

    @staticmethod
    def _task_row(task: Task, active_task_id: str | None) -> TuiTaskRow:
        return TuiTaskRow(
            id=task.id,
            title=task.title,
            status=task.status.value,
            is_active=task.id == active_task_id,
            is_terminal=task.is_terminal,
            completed_count=len(task.completed),
            remaining_count=len(task.remaining),
            updated_at=task.updated_at,
        )

    @staticmethod
    def _checkpoint_row(record: CheckpointRecord) -> TuiCheckpointRow:
        git = record.payload.git_state
        if git.status == RepositoryInspectionStatus.READY:
            branch = git.branch or "(detached)"
            summary = f"{branch} · {'dirty' if git.dirty else 'clean'}"
        else:
            summary = git.status.value
        return TuiCheckpointRow(
            id=record.id,
            kind=record.kind.value,
            session_id=record.session_id,
            created_at=record.created_at,
            git_summary=summary,
            test_reported=record.payload.test_status.known,
            record=record,
        )

    @staticmethod
    def _handoff_row(record: HandoffRecord) -> TuiHandoffRow:
        return TuiHandoffRow(
            id=record.id,
            source_provider_id=str(record.source_provider_id),
            target_provider_id=str(record.target_provider_id),
            status=record.status.value,
            checkpoint_id=record.source_checkpoint_id,
            created_at=record.created_at,
            record=record,
        )

    @staticmethod
    def _recovery_preview(report: RecoveryReport) -> TuiRecoveryPreview:
        return TuiRecoveryPreview(
            task_id=report.task_id,
            task_title=report.task_title,
            stale_session_ids=tuple(report.stale_session_ids),
            dirty=report.dirty,
            files_touched=tuple(report.files_touched),
            repository_status=report.repository_status.value,
        )
