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
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from cortexshift.adapters.git.inspector import GitRepositoryInspector
from cortexshift.adapters.process_runner import SubprocessInteractiveProcessRunner
from cortexshift.adapters.providers.antigravity import AntigravityHandoffAdapter
from cortexshift.adapters.providers.claude import ClaudeHandoffAdapter
from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.handoff_builder import HandoffBuilder
from cortexshift.application.handoff_renderer import HandoffRenderer, RenderedHandoffContext
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.native_session import native_capabilities, select_native_session
from cortexshift.application.run_service import ProviderRuntimeRegistry
from cortexshift.application.session_launcher import ProviderSessionLauncher
from cortexshift.application.source_session import select_source_session
from cortexshift.domain.errors import (
    GitProbeError,
    HandoffDeliveryError,
    NativeResumeError,
    NoActiveTaskError,
    ProjectNotInitializedError,
    ProviderNotFoundError,
    SameProviderSwitchError,
    TerminalRequiredError,
    UnknownProviderError,
    WorkspaceLockedError,
)
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.handoff import (
    HANDOFF_PROTOCOL_VERSION,
    HandoffFailureCode,
    HandoffPayload,
    HandoffRecord,
    HandoffStatus,
)
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.project import Project
from cortexshift.domain.session import Session, SessionExitReason
from cortexshift.domain.task import Task
from cortexshift.ports.handoff_delivery import ProviderHandoffAdapter
from cortexshift.ports.process_runner import InteractiveProcessRunner
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

    project_root: Path
    project: Project
    task: Task
    source_session: Session
    adapter: ProviderHandoffAdapter
    executable_path: str
    store: SQLiteStateStore
    prior_target: Session | None = None


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

    @staticmethod
    def _check_tty() -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    # --- Context resolution ---

    def _resolve_context(
        self,
        target_provider_name: str,
        from_session_id: str | None,
        start_dir: Path | str | None,
        require_executable: bool = True,
    ) -> _SwitchContext:
        """Resolve and validate project, active task, source session, and target provider.

        The source provider's executable is deliberately never probed: a handoff must not
        depend on the outgoing agent still being installed or usable.
        """
        start_path = Path(start_dir) if start_dir is not None else None
        project_root = ProjectLocator.find_project_root(start_path)
        if project_root is None:
            raise ProjectNotInitializedError()

        db_path = ProjectLocator.get_database_path(project_root)
        store = SQLiteStateStore(db_path, auto_migrate=False)

        try:
            project = store.get_default_project()
            if project is None:
                raise ProjectNotInitializedError()

            active_task_id = store.get_active_task_id(project.id)
            if not active_task_id:
                raise NoActiveTaskError()

            task = store.get_task(active_task_id)
            if task is None:
                raise NoActiveTaskError()

            adapter = self._registry.get(target_provider_name)
            if adapter is None:
                raise UnknownProviderError(
                    target_provider_name, self._registry.list_supported_ids()
                )

            source_session = select_source_session(store, task, from_session_id)

            if str(source_session.provider_id) == str(adapter.provider_id):
                raise SameProviderSwitchError(adapter.display_name)

            executable_path = ""
            if require_executable:
                resolved = self._which(adapter.executable)
                if not resolved:
                    raise ProviderNotFoundError(f"{adapter.display_name} was not found in PATH.")
                executable_path = resolved

            return _SwitchContext(
                project_root=project_root,
                project=project,
                task=task,
                source_session=source_session,
                adapter=adapter,
                executable_path=executable_path,
                store=store,
            )
        except Exception:
            store.close()
            raise

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
        return self._inspector.inspect(
            project_root=context.project_root,
            project_id=context.project.id,
        )

    # --- Non-mutating operations ---

    def preview(
        self,
        target_provider_name: str,
        from_session_id: str | None = None,
        note: str | None = None,
        start_dir: Path | str | None = None,
    ) -> HandoffPreviewResult:
        """Build and render the canonical handoff without persisting or launching anything.

        Persists no handoff, captures no Git snapshot, creates no target Session, acquires
        no long-lived workspace lease, runs no provider bootstrap, and consumes zero model
        quota.
        """
        context = self._resolve_context(
            target_provider_name,
            from_session_id,
            start_dir,
            require_executable=False,
        )
        try:
            inspection = self._inspect(context)
            payload = self._builder.build(
                project=context.project,
                task=context.task,
                source_session=context.source_session,
                inspection=inspection,
                target_provider_id=context.adapter.provider_id,
                snapshot_id=None,
                operator_note=note,
            )
            rendered = self._renderer.render(payload, handoff_id=None)

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
                context_truncated=rendered.truncated,
                context_omissions=[o.model_dump(mode="json") for o in rendered.omissions],
            )
        finally:
            context.store.close()

    def dry_run(
        self,
        target_provider_name: str,
        from_session_id: str | None = None,
        note: str | None = None,
        start_dir: Path | str | None = None,
        new_session: bool = False,
        resume_session_id: str | None = None,
    ) -> SwitchDryRunResult:
        """Describe what an actual switch would do without persisting or launching anything.

        Notably, a dry run of an Antigravity switch never performs the read-only plan
        bootstrap, so it consumes no model quota.
        """
        context = self._resolve_context(target_provider_name, from_session_id, start_dir)
        try:
            self._select_target(context, new_session, resume_session_id)
            inspection = self._inspect(context)
            payload = self._builder.build(
                project=context.project,
                task=context.task,
                source_session=context.source_session,
                inspection=inspection,
                target_provider_id=context.adapter.provider_id,
                snapshot_id=None,
                operator_note=note,
            )
            rendered = self._renderer.render(payload, handoff_id=None)
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
                context_truncated=rendered.truncated,
                context_omissions=[o.model_dump(mode="json") for o in rendered.omissions],
            )
        finally:
            context.store.close()

    # --- Executed switch ---

    def switch(
        self,
        target_provider_name: str,
        from_session_id: str | None = None,
        note: str | None = None,
        start_dir: Path | str | None = None,
        new_session: bool = False,
        resume_session_id: str | None = None,
        on_prepared: Callable[[HandoffRecord, RenderedHandoffContext], None] | None = None,
        on_launch: Callable[[LaunchSpecification, Session], None] | None = None,
    ) -> SwitchResult:
        """Hand the active task off to another provider and launch the receiving agent.

        The workspace lease is held continuously from repository observation through
        target provider runtime, so the Git state described by the handoff cannot drift
        under another CortexShift agent before the receiving agent starts.

        Task progress is never mutated: `switch` changes only handoff, session, and Git
        snapshot orchestration state.
        """
        context = self._resolve_context(target_provider_name, from_session_id, start_dir)
        store = context.store

        try:
            if not self._is_tty():
                raise TerminalRequiredError(
                    "Handing off to an interactive provider requires a terminal (TTY).\n\n"
                    f"To preview the switch non-interactively, use: "
                    f"cortexshift switch {target_provider_name} --dry-run"
                )

            lease = self._lease_manager.get_lease(Path(context.project.repo_path))
            if not lease.acquire():
                raise WorkspaceLockedError(lock_path=lease.lock_path)

            try:
                self._select_target(context, new_session, resume_session_id)
                inspection = self._inspect(context)
                snapshot_id = self._persist_snapshot(inspection, store)

                payload = self._builder.build(
                    project=context.project,
                    task=context.task,
                    source_session=context.source_session,
                    inspection=inspection,
                    target_provider_id=context.adapter.provider_id,
                    snapshot_id=snapshot_id,
                    operator_note=note,
                )

                handoff = HandoffRecord(
                    protocol_version=HANDOFF_PROTOCOL_VERSION,
                    project_id=context.project.id,
                    task_id=context.task.id,
                    source_session_id=context.source_session.id,
                    source_provider_id=context.source_session.provider_id,
                    target_provider_id=context.adapter.provider_id,
                    git_snapshot_id=snapshot_id,
                    status=HandoffStatus.PREPARED,
                    payload=payload,
                    metadata={
                        "delivery_strategy": context.adapter.delivery_strategy.value,
                    },
                )
                store.save_handoff(handoff)

                rendered = self._renderer.render(payload, handoff_id=handoff.id)
                if on_prepared:
                    on_prepared(handoff, rendered)

                return self._deliver(context, handoff, rendered, on_launch)
            finally:
                lease.release()
        finally:
            store.close()

    def _persist_snapshot(
        self,
        inspection: RepositoryInspection,
        store: SQLiteStateStore,
    ) -> str | None:
        """Persist the Git snapshot when Git is ready, honestly handling other states.

        A known `git_not_installed` or `not_git_repository` state continues the handoff
        with an explicit canonical marker and no snapshot row. An unexpected probe error
        fails the switch instead of producing a package whose repository observation may
        be unreliable. Fake snapshot rows are never created.
        """
        if inspection.status == RepositoryInspectionStatus.PROBE_ERROR:
            raise GitProbeError(
                inspection.diagnostic
                or "Git repository inspection failed; refusing to build a handoff "
                "from an unreliable repository observation."
            )

        if inspection.status != RepositoryInspectionStatus.READY or inspection.snapshot is None:
            return None

        store.save_snapshot(inspection.snapshot)
        return inspection.snapshot.id

    def _deliver(
        self,
        context: _SwitchContext,
        handoff: HandoffRecord,
        rendered: RenderedHandoffContext,
        on_launch: Callable[[LaunchSpecification, Session], None] | None,
    ) -> SwitchResult:
        """Deliver the rendered context through the target provider's strategy."""
        store = context.store
        launcher = ProviderSessionLauncher(process_runner=self._runner, store=store)

        target_session = launcher.start_session(
            task_id=context.task.id,
            provider_id=context.adapter.provider_id,
            metadata={"handoff_id": handoff.id},
            resumed_from_session_id=context.prior_target.id if context.prior_target else None,
            native_session_id=(
                context.prior_target.native_session_id if context.prior_target else None
            ),
        )

        try:
            preparation = context.adapter.prepare_delivery(
                executable_path=context.executable_path,
                project_root=Path(context.project.repo_path),
                rendered_context=rendered.text,
                native_session_id=(
                    context.prior_target.native_session_id if context.prior_target else None
                ),
            )
        except HandoffDeliveryError as err:
            target_session = launcher.fail_session(target_session, SessionExitReason.SPAWN_FAILED)
            self._fail_handoff(store, handoff, err.failure_code, target_session.id)
            raise

        except BaseException as err:
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

        if preparation.native_session_id:
            target_session = launcher.attach_native_session_id(
                target_session, preparation.native_session_id
            )

        delivered = handoff.mark_delivered(
            target_session_id=target_session.id,
            delivered_at=utc_now(),
        )
        store.update_handoff_delivery(
            delivered.id,
            status=HandoffStatus.DELIVERED,
            target_session_id=target_session.id,
            delivered_at=delivered.delivered_at,
        )

        try:
            target_session = launcher.run(
                target_session, preparation.launch_spec, on_launch=on_launch
            )
        except Exception:
            # The interactive process never started, so context was not actually delivered.
            self._fail_handoff(
                store, delivered, HandoffFailureCode.SPAWN_FAILED.value, target_session.id
            )
            raise

        return SwitchResult(
            handoff=delivered,
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
