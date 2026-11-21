"""Select and continue a managed Session on the verified active Task."""

from collections.abc import Callable
from contextlib import closing

from pydantic import Field

from cortexshift.application.native_session import is_native_resumable, select_native_session
from cortexshift.application.run_service import DryRunResult, RunService
from cortexshift.application.session_launcher import ProviderSessionLauncher
from cortexshift.application.source_session import select_source_session
from cortexshift.application.task_service import TaskService
from cortexshift.application.workspace import WorkspaceIdentity, WorkspaceStart, open_workspace
from cortexshift.domain.errors import (
    NativeResumeError,
    SessionRecoveryError,
    TerminalRequiredError,
    WorkspaceLockedError,
)
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.session import (
    CONTINUED_FROM_SESSION_KEY,
    STARTUP_OBJECTIVE_SERVED_KEY,
    UNFINALIZED_SESSION_STATUSES,
    Session,
)
from cortexshift.domain.task import Task
from cortexshift.ports.native_session import ProviderNativeSessionAdapter
from cortexshift.ports.provider import CONTINUATION_PROMPT, ManagedMcpBinder, ProviderRuntimeAdapter
from cortexshift.ports.session_store import SessionStore


class ResumeDryRunResult(DryRunResult):
    source_session_id: str
    native_session_id: str | None
    native_session_known: bool = True
    resume_strategy: str = "exact_native_id"
    bootstrap_model_turn_required: bool = False
    automatic_context: bool = False
    objective_length: int = Field(exclude=True)

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class ResumeService(RunService):
    """Reuse provider discovery, the workspace lease, and invocation lifecycle."""

    def resume(
        self,
        provider_name: str | None = None,
        session_id: str | None = None,
        start_dir: WorkspaceStart = None,
        *,
        dry_run: bool = False,
        new_session: bool = False,
        on_launch: Callable[[ResumeDryRunResult], None] | None = None,
    ) -> Session | ResumeDryRunResult:
        if new_session and session_id is not None:
            raise NativeResumeError("--new-session and --session are mutually exclusive.")

        workspace, store = open_workspace(start_dir)
        with closing(store):
            tasks = TaskService(store)
            task = tasks.require_active_task(workspace.project.id)
            if dry_run:
                _, _, preview = self._plan(
                    workspace, store, task, provider_name, session_id, new_session
                )
                return preview
            if not self._is_tty():
                raise TerminalRequiredError(
                    "Interactive resume requires a terminal (TTY). "
                    "Use cortexshift resume --dry-run to preview."
                )
            workspace.revalidate()
            lease = self._lease_manager.get_lease(workspace.root)
            if not lease.acquire():
                raise WorkspaceLockedError(lock_path=lease.lock_path)
            try:
                with store.write_transaction():
                    task = tasks.require_active_task(workspace.project.id)
                    adapter, spec, plan = self._plan(
                        workspace, store, task, provider_name, session_id, new_session
                    )
                    launcher = ProviderSessionLauncher(
                        self._runner, store, self._checkpoint_service, workspace
                    )
                    metadata: dict[str, int | str] = {}
                    if plan.automatic_context:
                        metadata[STARTUP_OBJECTIVE_SERVED_KEY] = 0
                    if not plan.native_session_known:
                        metadata[CONTINUED_FROM_SESSION_KEY] = plan.source_session_id
                    invocation = launcher.start_session(
                        task.id,
                        adapter.provider_id,
                        native_session_id=spec.native_session_id,
                        resumed_from_session_id=plan.source_session_id
                        if plan.native_session_known
                        else None,
                        metadata=metadata,
                    )
                return launcher.run(
                    invocation,
                    spec,
                    on_launch=(lambda *_: on_launch(plan)) if on_launch else None,
                    mcp_binder=adapter if isinstance(adapter, ManagedMcpBinder) else None,
                )
            finally:
                lease.release()

    def _plan(
        self,
        workspace: WorkspaceIdentity,
        store: SessionStore,
        task: Task,
        provider_name: str | None,
        session_id: str | None,
        new_session: bool,
    ) -> tuple[ProviderRuntimeAdapter, LaunchSpecification, ResumeDryRunResult]:
        adapter = self._get_adapter(provider_name) if provider_name is not None else None
        recent = select_source_session(
            store, task, session_id, provider_id=adapter.provider_id if adapter else None
        )
        if recent.status in UNFINALIZED_SESSION_STATUSES:
            raise SessionRecoveryError(
                f"Session {recent.id} has no recorded end. Check cortexshift status, "
                "then run cortexshift recover --dry-run when the workspace is free."
            )
        adapter = adapter or self._get_adapter(str(recent.provider_id))
        executable = self._resolve_executable(adapter)
        source = None
        if not new_session and isinstance(adapter, ProviderNativeSessionAdapter):
            capabilities = adapter.get_native_capabilities()
            if provider_name is not None or session_id is not None:
                source = select_native_session(
                    store, task.id, adapter.provider_id, capabilities, session_id
                )
            elif is_native_resumable(recent, capabilities):
                source = recent

        managed_context = isinstance(adapter, ManagedMcpBinder)
        if source is not None:
            assert isinstance(adapter, ProviderNativeSessionAdapter)
            assert source.native_session_id is not None
            if (
                managed_context
                and adapter.get_native_capabilities().can_resume_with_followup_context
            ):
                spec = adapter.build_exact_resume(
                    workspace.root, executable, source.native_session_id, prompt=CONTINUATION_PROMPT
                )
            else:
                spec = adapter.build_exact_resume(
                    workspace.root, executable, source.native_session_id
                )
        elif session_id is not None or (provider_name is not None and not new_session):
            raise NativeResumeError(
                f"No exact resumable {adapter.display_name} native session is recorded "
                "for the active task. CortexShift will not guess a provider session. "
                f"Use cortexshift resume {adapter.provider_id} --new-session to continue "
                "in a fresh conversation where supported."
            )
        elif not managed_context:
            raise NativeResumeError(
                f"{adapter.display_name} cannot start a fresh conversation with automatic "
                "Task context through resume. Use cortexshift run "
                f"{adapter.provider_id} for manual continuation, or switch to another provider."
            )
        else:
            spec = adapter.build_launch_spec(workspace.root, executable, CONTINUATION_PROMPT)
            if not spec.interactive or not spec.prompt_supplied:
                raise NativeResumeError(
                    f"Could not prepare automatic Task context for {adapter.display_name}."
                )

        workspace.validate_launch_root(spec.cwd)
        exact = source is not None
        return (
            adapter,
            spec,
            ResumeDryRunResult(
                provider_id=adapter.provider_id,
                display_name=adapter.display_name,
                executable=executable,
                project_id=workspace.project.id,
                project_name=workspace.project.name,
                task_id=task.id,
                task_title=task.title,
                cwd=spec.cwd,
                argv=spec.to_redacted_argv(),
                mode="native_resume" if exact else "task_continuation",
                prompt_supplied=spec.prompt_supplied,
                source_session_id=(source or recent).id,
                native_session_id=source.native_session_id if source else None,
                native_session_known=exact,
                resume_strategy="exact_native_id" if exact else "fresh_task_context",
                automatic_context=spec.prompt_supplied and managed_context,
                objective_length=len(task.objective),
            ),
        )
