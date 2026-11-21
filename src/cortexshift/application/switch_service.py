"""Application orchestration for manual agent switching and canonical handoff delivery.

`SwitchService` is the service behind CortexShift's central product promise: one Task,
multiple coding agents, no need to manually re-explain the work.

Critically, the outgoing agent is never required. The handoff is derived deterministically
from durable local state — canonical Project, canonical Task, previous CortexShift Session
metadata, live repository inspection, and the Git snapshot captured at switch time — so
switching still works after the outgoing provider's quota is exhausted, its process is
gone, or its CLI is no longer even installed. No outgoing model call is ever made.

The service renders no Rich output, knows nothing about Typer, contains no SQL, and
constructs no provider-specific commands; provider variance lives behind handoff
delivery adapters.
"""

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.process_runner import SubprocessInteractiveProcessRunner
from cortexshift.adapters.providers.antigravity import AntigravityHandoffAdapter
from cortexshift.adapters.providers.claude import ClaudeHandoffAdapter
from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.checkpoint_service import CheckpointService
from cortexshift.application.handoff_builder import HandoffBuilder, unique_checkpoint_decisions
from cortexshift.application.handoff_renderer import (
    OBJECTIVE_VIA_MCP_KEY,
    HandoffRenderer,
    RenderedHandoffContext,
)
from cortexshift.application.native_session import native_capabilities, select_native_session
from cortexshift.application.run_service import ProviderRuntimeRegistry
from cortexshift.application.session_launcher import ProviderSessionLauncher
from cortexshift.application.source_session import select_source_session
from cortexshift.application.task_service import TaskService
from cortexshift.application.workspace import WorkspaceIdentity, WorkspaceStart, open_workspace
from cortexshift.domain.errors import (
    GitProbeError,
    HandoffDeliveryError,
    NativeResumeError,
    ProjectConflictError,
    ProviderNotFoundError,
    SameProviderSwitchError,
    TerminalRequiredError,
    UnknownProviderError,
    WorkspaceLockedError,
)
from cortexshift.domain.git import GitSnapshot, RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.handoff import (
    HANDOFF_PROTOCOL_VERSION,
    HandoffFailureCode,
    HandoffPayload,
    HandoffRecord,
    HandoffStatus,
    generate_handoff_id,
)
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.project import Project
from cortexshift.domain.session import STARTUP_OBJECTIVE_SERVED_KEY, Session, SessionExitReason
from cortexshift.domain.task import Task
from cortexshift.ports.handoff_delivery import ProviderHandoffAdapter
from cortexshift.ports.process_runner import InteractiveProcessRunner
from cortexshift.ports.provider import ManagedMcpBinder
from cortexshift.ports.repository import RepositoryInspector
from cortexshift.ports.workspace_lease import WorkspaceLeaseManager


class ProviderHandoffRegistry:
    """Registry of supported provider handoff delivery adapters."""

    def __init__(self, adapters: list[ProviderHandoffAdapter] | None = None) -> None:
        self._adapters: dict[str, ProviderHandoffAdapter] = {}
        if adapters is None:
            adapters = [
                ClaudeHandoffAdapter(),
                CodexHandoffAdapter(),
                AntigravityHandoffAdapter(),
            ]
        for adapter in adapters:
            self._adapters[str(adapter.provider_id).lower()] = adapter

    def get(self, provider_id_str: str) -> ProviderHandoffAdapter | None:
        return self._adapters.get(provider_id_str.strip().lower())

    def list_supported_ids(self) -> list[str]:
        return sorted(self._adapters.keys())


class SwitchDryRunResult(BaseModel):
    """Preview of what an actual switch would do, without side effects of any kind."""

    model_config = ConfigDict(frozen=True)

    protocol_version: int = HANDOFF_PROTOCOL_VERSION
    project_id: str
    project_name: str
    task_id: str
    task_title: str

    source_session_id: str
    source_provider_id: str
    source_session_status: str

    target_provider_id: str
    target_provider_name: str
    target_native_mode: str = "new_session"
    selected_prior_target_session_id: str | None = None
    native_session_known: bool = False
    native_session_id: str | None = None
    target_executable: str

    git_status: str
    git_branch: str | None = None
    git_head_sha: str | None = None
    git_dirty: bool = False

    delivery_strategy: str
    bootstrap_model_turn_required: bool

    context_characters: int
    context_max_characters: int
    context_bytes: int
    context_max_bytes: int
    context_truncated: bool
    context_omissions: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        """Serialize for machine-readable output."""
        return self.model_dump(mode="json")


class HandoffPreviewResult(BaseModel):
    """A rendered, unpersisted preview of the canonical handoff for a target provider."""

    model_config = ConfigDict(frozen=True)

    protocol_version: int = HANDOFF_PROTOCOL_VERSION
    project_id: str
    target_provider_id: str
    target_provider_name: str
    delivery_strategy: str
    bootstrap_model_turn_required: bool
    payload: HandoffPayload
    rendered_context: str
    context_characters: int
    context_max_characters: int
    context_bytes: int
    context_max_bytes: int
    context_truncated: bool
    context_omissions: list[dict[str, Any]] = []

    def to_dict(self) -> dict[str, Any]:
        """Serialize for machine-readable output."""
        return self.model_dump(mode="json")


class SwitchResult(BaseModel):
    """Outcome of an executed switch.

    Handoff delivery and target Session outcome are deliberately separate: context can be
    delivered successfully and the receiving provider can still exit non-zero afterwards.
    """

    model_config = ConfigDict(frozen=True)

    handoff: HandoffRecord
    target_session: Session
    bootstrap_performed: bool = False


@dataclass
class _SwitchContext:
    """Resolved, validated inputs for a handoff operation."""

    workspace: WorkspaceIdentity
    task: Task
    source_session: Session
    adapter: ProviderHandoffAdapter
    executable_path: str
    store: SQLiteStateStore
    prior_target: Session | None = None

    @property
    def project(self) -> Project:
        return self.workspace.project


class SwitchService:
    """Orchestrates canonical handoff generation and delivery to a receiving provider."""

    def __init__(
        self,
        registry: ProviderHandoffRegistry | None = None,
        native_registry: ProviderRuntimeRegistry | None = None,
        inspector: RepositoryInspector | None = None,
        process_runner: InteractiveProcessRunner | None = None,
        lease_manager: WorkspaceLeaseManager | None = None,
        builder: HandoffBuilder | None = None,
        renderer: HandoffRenderer | None = None,
        which_fn: Callable[[str], str | None] | None = None,
        is_tty_fn: Callable[[], bool] | None = None,
        checkpoint_service: CheckpointService | None = None,
    ) -> None:
        self._registry = registry or ProviderHandoffRegistry()
        self._native_registry = native_registry or ProviderRuntimeRegistry()
        self._inspector = inspector or GitRepositoryInspector()
        self._runner = process_runner or SubprocessInteractiveProcessRunner()
        self._lease_manager = lease_manager or FileWorkspaceLeaseManager()
        self._builder = builder or HandoffBuilder()
        self._renderer = renderer or HandoffRenderer()
        self._which = which_fn if which_fn is not None else (lambda cmd: shutil.which(cmd))
        self._is_tty = is_tty_fn if is_tty_fn is not None else self._check_tty
        self._checkpoint_service = checkpoint_service or CheckpointService(
            inspector=self._inspector
        )

    @staticmethod
    def _check_tty() -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    # --- Context resolution ---

    def _resolve_context(
        self,
        workspace: WorkspaceIdentity,
        store: SQLiteStateStore,
        target_provider_name: str,
        from_session_id: str | None,
        require_executable: bool = True,
    ) -> _SwitchContext:
        """Resolve and validate project, active task, source session, and target provider.

        The source provider's executable is deliberately never probed: a handoff must not
        depend on the outgoing agent still being installed or usable.
        """
        task = TaskService(store).require_active_task(workspace.project.id)
        adapter = self._registry.get(target_provider_name)
        if adapter is None:
            raise UnknownProviderError(target_provider_name, self._registry.list_supported_ids())
        source_session = select_source_session(store, task, from_session_id)
        if source_session.provider_id == adapter.provider_id:
            raise SameProviderSwitchError(adapter.display_name)

        executable_path = ""
        if require_executable:
            resolved = self._which(adapter.executable)
            if not resolved:
                raise ProviderNotFoundError(
                    f"{adapter.display_name} was not found in PATH. "
                    "Install its native CLI and check cortexshift doctor before retrying."
                )
            executable_path = resolved

        return _SwitchContext(
            workspace=workspace,
            task=task,
            source_session=source_session,
            adapter=adapter,
            executable_path=executable_path,
            store=store,
        )

    def _select_target(
        self, context: _SwitchContext, new_session: bool, resume_session_id: str | None
    ) -> None:
        if new_session and resume_session_id is not None:
            raise NativeResumeError("--new-session and --resume-session are mutually exclusive.")
        adapter = self._native_registry.get(str(context.adapter.provider_id))
        if new_session:
            context.prior_target = None
        elif (capabilities := native_capabilities(adapter)).supports_exact_resume:
            if capabilities.can_resume_with_followup_context:
                context.prior_target = select_native_session(
                    context.store,
                    context.task.id,
                    context.adapter.provider_id,
                    capabilities,
                    resume_session_id,
                )
            elif resume_session_id is not None:
                raise NativeResumeError("Provider cannot receive fresh context on exact resume.")
        elif resume_session_id is not None:
            raise NativeResumeError("Provider does not support exact resume.")

    def _inspect(self, context: _SwitchContext) -> RepositoryInspection:
        """Inspect the live repository for the project being handed off."""
        context.workspace.revalidate()
        return self._inspector.inspect(
            project_root=context.workspace.root,
            project_id=context.project.id,
        )

    def _build_payload(
        self,
        context: _SwitchContext,
        inspection: RepositoryInspection,
        note: str | None,
        snapshot_id: str | None = None,
    ) -> HandoffPayload:
        store, project, task = context.store, context.project, context.task
        decisions = unique_checkpoint_decisions(
            store.list_checkpoint_decisions(project.id, task.id)
        )
        evidence = store.get_latest_checkpoint_test_evidence(project.id, task.id)
        latest = store.get_latest_checkpoint_reference(project.id, task.id)
        return self._builder.build(
            project=project,
            task=task,
            source_session=context.source_session,
            inspection=inspection,
            target_provider_id=context.adapter.provider_id,
            snapshot_id=snapshot_id,
            operator_note=note,
            latest_checkpoint=latest,
            task_decisions=decisions,
            test_evidence=evidence,
        )

    # --- Non-mutating operations ---

    def _render_for_switch(
        self, context: _SwitchContext, payload: HandoffPayload, handoff_id: str
    ) -> RenderedHandoffContext:
        overhead = context.adapter.context_overhead_bytes
        try:
            return self._renderer.render(payload, handoff_id, overhead_bytes=overhead)
        except HandoffDeliveryError as err:
            if err.failure_code != HandoffFailureCode.CONTEXT_TOO_LARGE or not isinstance(
                context.adapter, ManagedMcpBinder
            ):
                raise
        return self._renderer.render(
            payload, handoff_id, overhead_bytes=overhead, objective_via_mcp=True
        )

    def preview(
        self,
        target_provider_name: str,
        from_session_id: str | None = None,
        note: str | None = None,
        start_dir: WorkspaceStart = None,
    ) -> HandoffPreviewResult:
        """Build and render the canonical handoff without persisting or launching anything.

        Persists no handoff, captures no Git snapshot, creates no target Session, acquires
        no long-lived workspace lease, runs no provider bootstrap, and consumes zero model
        quota.
        """
        workspace, store = open_workspace(start_dir)
        try:
            context = self._resolve_context(
                workspace, store, target_provider_name, from_session_id, require_executable=False
            )
            inspection = self._inspect(context)
            payload = self._build_payload(context, inspection, note)
            rendered = self._renderer.render(
                payload, overhead_bytes=context.adapter.context_overhead_bytes
            )

            return HandoffPreviewResult(
                project_id=context.project.id,
                target_provider_id=str(context.adapter.provider_id),
                target_provider_name=context.adapter.display_name,
                delivery_strategy=context.adapter.delivery_strategy.value,
                bootstrap_model_turn_required=context.adapter.bootstrap_model_turn_required,
                payload=payload,
                rendered_context=rendered.text,
                context_characters=rendered.character_count,
                context_max_characters=rendered.max_characters,
                context_bytes=rendered.byte_count,
                context_max_bytes=rendered.max_bytes,
                context_truncated=rendered.truncated,
                context_omissions=[o.model_dump(mode="json") for o in rendered.omissions],
            )
        finally:
            store.close()

    def dry_run(
        self,
        target_provider_name: str,
        from_session_id: str | None = None,
        note: str | None = None,
        start_dir: WorkspaceStart = None,
        new_session: bool = False,
        resume_session_id: str | None = None,
    ) -> SwitchDryRunResult:
        """Describe what an actual switch would do without persisting or launching anything.

        Notably, a dry run of an Antigravity switch never performs the read-only plan
        bootstrap, so it consumes no model quota.
        """
        workspace, store = open_workspace(start_dir)
        try:
            context = self._resolve_context(workspace, store, target_provider_name, from_session_id)
            self._select_target(context, new_session, resume_session_id)
            inspection = self._inspect(context)
            payload = self._build_payload(context, inspection, note)
            # Include an ID-shaped label so preview and execution reserve the same space.
            rendered = self._render_for_switch(context, payload, generate_handoff_id())
            snapshot = inspection.snapshot

            return SwitchDryRunResult(
                project_id=context.project.id,
                project_name=context.project.name,
                task_id=context.task.id,
                task_title=context.task.title,
                source_session_id=context.source_session.id,
                source_provider_id=str(context.source_session.provider_id),
                source_session_status=context.source_session.status.value,
                target_provider_id=str(context.adapter.provider_id),
                target_provider_name=context.adapter.display_name,
                target_executable=context.executable_path,
                target_native_mode="resume_existing" if context.prior_target else "new_session",
                selected_prior_target_session_id=(
                    context.prior_target.id if context.prior_target else None
                ),
                native_session_known=context.prior_target is not None,
                native_session_id=(
                    context.prior_target.native_session_id if context.prior_target else None
                ),
                git_status=inspection.status.value,
                git_branch=snapshot.branch if snapshot else None,
                git_head_sha=snapshot.head_sha if snapshot else None,
                git_dirty=snapshot.dirty if snapshot else False,
                delivery_strategy=context.adapter.delivery_strategy.value,
                bootstrap_model_turn_required=context.adapter.bootstrap_model_turn_required,
                context_characters=rendered.character_count,
                context_max_characters=rendered.max_characters,
                context_bytes=rendered.byte_count,
                context_max_bytes=rendered.max_bytes,
                context_truncated=rendered.truncated,
                context_omissions=[o.model_dump(mode="json") for o in rendered.omissions],
            )
        finally:
            store.close()

    # --- Executed switch ---

    def switch(
        self,
        target_provider_name: str,
        from_session_id: str | None = None,
        note: str | None = None,
        start_dir: WorkspaceStart = None,
        new_session: bool = False,
        resume_session_id: str | None = None,
        on_prepared: Callable[[HandoffRecord, RenderedHandoffContext], None] | None = None,
        on_launch: Callable[[LaunchSpecification, Session], None] | None = None,
    ) -> SwitchResult:
        """Hand the active task off to another provider and launch the receiving agent.

        The workspace lease is held from repository observation through target runtime.
        Its preflight also checks for retained provider work. External and untracked
        processes can still change the repository during a handoff.

        Task progress is never mutated: `switch` changes only handoff, session, and Git
        snapshot orchestration state.
        """
        workspace, store = open_workspace(start_dir)
        try:
            context = self._resolve_context(workspace, store, target_provider_name, from_session_id)
            if not self._is_tty():
                raise TerminalRequiredError(
                    "Handing off to an interactive provider requires a terminal (TTY).\n\n"
                    f"To preview the switch non-interactively, use: "
                    f"cortexshift switch {target_provider_name} --dry-run"
                )

            context.workspace.revalidate()
            lease = self._lease_manager.get_lease(context.workspace.root)
            if not lease.acquire():
                raise WorkspaceLockedError(lock_path=lease.lock_path)

            try:
                # A previous invocation may have completed before we acquired the lease.
                context = self._resolve_context(
                    workspace, store, target_provider_name, from_session_id
                )
                self._select_target(context, new_session, resume_session_id)
                inspection = self._inspect(context)
                context.workspace.revalidate()
                snapshot = self._handoff_snapshot(inspection)
                snapshot_id = snapshot.id if snapshot else None

                payload = self._build_payload(context, inspection, note, snapshot_id)

                handoff_id = generate_handoff_id()
                rendered = self._render_for_switch(context, payload, handoff_id)
                handoff = HandoffRecord(
                    id=handoff_id,
                    protocol_version=HANDOFF_PROTOCOL_VERSION,
                    project_id=context.project.id,
                    task_id=context.task.id,
                    source_session_id=context.source_session.id,
                    source_provider_id=context.source_session.provider_id,
                    target_provider_id=context.adapter.provider_id,
                    source_checkpoint_id=payload.source_checkpoint_id,
                    git_snapshot_id=snapshot_id,
                    status=HandoffStatus.PREPARED,
                    payload=payload,
                    metadata={
                        "delivery_strategy": context.adapter.delivery_strategy.value,
                        "context_max_bytes": rendered.max_bytes,
                        OBJECTIVE_VIA_MCP_KEY: any(
                            omission.section == "original_objective"
                            for omission in rendered.omissions
                        ),
                    },
                )
                return self._deliver(context, handoff, rendered, snapshot, on_prepared, on_launch)
            finally:
                lease.release()
        finally:
            store.close()

    @staticmethod
    def _handoff_snapshot(inspection: RepositoryInspection) -> GitSnapshot | None:
        """Select the live observation eligible for persistence after rendering."""
        if inspection.status == RepositoryInspectionStatus.PROBE_ERROR:
            raise GitProbeError(
                inspection.diagnostic
                or "Git repository inspection failed; refusing to build a handoff "
                "from an unreliable repository observation."
            )

        return inspection.snapshot if inspection.is_ready else None

    def _deliver(
        self,
        context: _SwitchContext,
        handoff: HandoffRecord,
        rendered: RenderedHandoffContext,
        snapshot: GitSnapshot | None,
        on_prepared: Callable[[HandoffRecord, RenderedHandoffContext], None] | None,
        on_launch: Callable[[LaunchSpecification, Session], None] | None,
    ) -> SwitchResult:
        """Deliver the rendered context through the target provider's strategy."""
        store = context.store
        launcher = ProviderSessionLauncher(
            process_runner=self._runner,
            store=store,
            checkpoint_service=self._checkpoint_service,
            workspace=context.workspace,
        )
        binder = context.adapter if isinstance(context.adapter, ManagedMcpBinder) else None
        session_metadata: dict[str, str | int] = {"handoff_id": handoff.id}
        if binder is not None:
            session_metadata[STARTUP_OBJECTIVE_SERVED_KEY] = 0

        # Persist both bindings before any provider work can request the handoff.
        with store.write_transaction():
            task = TaskService(store).require_active_task(context.project.id)
            if task.id != context.task.id:
                raise NativeResumeError("The active Task changed during switch. Retry the switch.")
            if snapshot is not None:
                store.save_snapshot(snapshot)
            target_session = launcher.start_session(
                task_id=context.task.id,
                provider_id=context.adapter.provider_id,
                metadata=session_metadata,
                resumed_from_session_id=context.prior_target.id if context.prior_target else None,
                native_session_id=(
                    context.prior_target.native_session_id if context.prior_target else None
                ),
            )

            handoff = handoff.model_copy(update={"target_session_id": target_session.id})
            store.save_handoff(handoff)

        try:
            if on_prepared:
                on_prepared(handoff, rendered)
            preparation = context.adapter.prepare_delivery(
                executable_path=context.executable_path,
                project_root=context.workspace.root,
                rendered_context=rendered.text,
                native_session_id=(
                    context.prior_target.native_session_id if context.prior_target else None
                ),
            )
        except HandoffDeliveryError as err:
            context.workspace.revalidate()
            target_session = launcher.fail_session(target_session, SessionExitReason.SPAWN_FAILED)
            self._fail_handoff(store, handoff, err.failure_code, target_session.id)
            raise

        except BaseException as err:
            context.workspace.revalidate()
            launcher.fail_session(target_session, SessionExitReason.SPAWN_FAILED)
            self._fail_handoff(
                store, handoff, HandoffFailureCode.BOOTSTRAP_FAILED.value, target_session.id
            )
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            raise HandoffDeliveryError(
                "bootstrap_failed",
                "Provider handoff preparation failed; no fallback was attempted.",
            ) from None

        context.workspace.revalidate()
        if preparation.native_session_id:
            target_session = launcher.attach_native_session_id(
                target_session, preparation.native_session_id
            )

        if preparation.bootstrap_performed:
            store.update_handoff_delivery(
                handoff.id,
                status=HandoffStatus.DELIVERED,
                target_session_id=target_session.id,
                delivered_at=utc_now(),
            )

        try:
            target_session = launcher.run(
                target_session,
                preparation.launch_spec,
                on_launch=on_launch,
                mcp_binder=binder,
            )
        except ProjectConflictError:
            raise
        except Exception:
            # The switch requires an interactive destination even after a bootstrap receipt.
            self._fail_handoff(
                store, handoff, HandoffFailureCode.SPAWN_FAILED.value, target_session.id
            )
            raise

        recorded_handoff = store.get_handoff(handoff.id)
        assert recorded_handoff is not None
        return SwitchResult(
            handoff=recorded_handoff,
            target_session=target_session,
            bootstrap_performed=preparation.bootstrap_performed,
        )

    @staticmethod
    def _fail_handoff(
        store: SQLiteStateStore,
        handoff: HandoffRecord,
        failure_code: str,
        target_session_id: str | None,
    ) -> None:
        """Record a safe machine classification for a failed handoff delivery."""
        try:
            code = HandoffFailureCode(failure_code)
        except ValueError:
            code = HandoffFailureCode.BOOTSTRAP_FAILED
        store.update_handoff_delivery(
            handoff.id,
            status=HandoffStatus.FAILED,
            target_session_id=target_session_id,
            failure_code=code,
        )
