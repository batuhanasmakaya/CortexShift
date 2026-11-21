"""Desktop adapter over the canonical workspace, task and provider services."""

import os
import shutil
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cortexshift.adapters.discovery import BuiltinProviderDiscovery
from cortexshift.adapters.process_runner import SubprocessInteractiveProcessRunner
from cortexshift.adapters.workspace_lease import FileWorkspaceLease, FileWorkspaceLeaseManager
from cortexshift.application.doctor import DoctorService
from cortexshift.application.recovery_service import RecoveryService
from cortexshift.application.resume_service import ResumeDryRunResult, ResumeService
from cortexshift.application.run_service import ProviderRuntimeRegistry
from cortexshift.application.start_service import StartService
from cortexshift.application.status_service import ProjectStatusService
from cortexshift.application.switch_service import SwitchService
from cortexshift.application.task_service import TaskService
from cortexshift.application.workspace import WorkspaceIdentity, open_workspace, read_workspace
from cortexshift.domain.errors import (
    ActiveTaskChangedError,
    CortexShiftError,
    SessionRecoveryError,
    WorkspaceLockedError,
)
from cortexshift.domain.provider import ProviderId
from cortexshift.domain.session import UNFINALIZED_SESSION_STATUSES, Session
from cortexshift.ports.provider import ManagedMcpBinder
from cortexshift.ports.workspace_lease import WorkspaceLease

type Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.-]+$")]


class AgentAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: Literal["start", "resume", "continue", "switch"]
    task_id: Identifier
    provider: Identifier | None = None
    session_id: Identifier | None = None
    fresh_target: bool = False

    @model_validator(mode="after")
    def validate_action(self) -> "AgentAction":
        if self.kind in ("start", "switch") and self.provider is None:
            raise ValueError("A destination provider is required.")
        if self.kind == "start" and self.session_id is not None:
            raise ValueError("Start does not take a source Session.")
        if self.fresh_target and self.kind != "switch":
            raise ValueError("A fresh target applies only to switch.")
        return self


class AgentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    provider: Identifier
    mode: Literal["start", "native_resume", "task_continuation", "switch_native", "switch_fresh"]
    source: Identifier | None = None
    target: Identifier | None = None
    bootstrap_turn: bool = False


class UnsupportedDesktopProvider(CortexShiftError):
    pass


def verified(root: Path, project_id: str) -> WorkspaceIdentity:
    with read_workspace(root, expected_project_id=project_id) as (workspace, _):
        return workspace


def provider_path(root: Path) -> str:
    """Use absolute local PATH entries and common native installation locations."""
    paths = [Path(part) for part in os.get_exec_path() if part and Path(part).is_absolute()]
    if os.name != "nt":
        paths.extend(
            [Path.home() / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]
        )
    return os.pathsep.join(
        str(path) for path in dict.fromkeys(paths) if not path.resolve().is_relative_to(root)
    )


def provider_lookup(root: Path, path: str) -> Callable[[str], str | None]:
    def find(executable: str) -> str | None:
        # An explicit path and a non-relative lookup avoid project-local executables.
        if not path:
            return None
        resolved = shutil.which(f"{executable}.exe" if os.name == "nt" else executable, path=path)
        if (
            resolved
            and Path(resolved).is_absolute()
            and not Path(resolved).resolve().is_relative_to(root)
        ):
            return resolved
        return None

    return find


class DesktopActionLease(FileWorkspaceLease):
    """Recheck Desktop's execution prerequisites while holding the core lease."""

    def __init__(self, lease: WorkspaceLease, workspace: WorkspaceIdentity, action: AgentAction):
        super().__init__(lease.lock_path)
        self._workspace = workspace
        self._action = action

    def acquire(self) -> bool:
        if not super().acquire():
            return False
        try:
            DesktopControl.check_action(self._workspace, self._action, probe=False)
        except BaseException:
            self.release()
            raise
        return True


class DesktopLeaseManager(FileWorkspaceLeaseManager):
    def __init__(self, workspace: WorkspaceIdentity, action: AgentAction) -> None:
        self._workspace = workspace
        self._action = action

    def get_lease(self, project_root: Path) -> WorkspaceLease:
        self._workspace.validate_launch_root(project_root)
        return DesktopActionLease(super().get_lease(project_root), self._workspace, self._action)


class DesktopControl:
    def create(
        self,
        workspace: WorkspaceIdentity,
        task_id: str,
        title: str,
        objective: str,
        activate: bool,
        expected_active: str | None,
    ) -> dict[str, Any]:
        _, store = open_workspace(workspace)
        with closing(store):
            task = TaskService(store).start_task(
                workspace.project.id,
                title,
                objective,
                set_active=activate,
                require_no_active=activate and expected_active is None,
                replace_active_id=expected_active if activate else None,
                creation_id=task_id,
            )
            return {
                "id": task.id,
                "active": store.get_active_task_id(workspace.project.id) == task.id,
            }

    def activate(
        self,
        workspace: WorkspaceIdentity,
        task_id: str,
        expected_active: str | None,
    ) -> dict[str, Any]:
        _, store = open_workspace(workspace)
        with closing(store):
            task = TaskService(store).activate_task(
                workspace.project.id, task_id, expected_active=(expected_active,)
            )
            return {"id": task.id}

    def providers(self, workspace: WorkspaceIdentity) -> dict[str, Any]:
        registry = ProviderRuntimeRegistry()
        find = provider_lookup(workspace.root, provider_path(workspace.root))
        return {
            "providers": [
                {
                    "id": name,
                    "managed": isinstance(adapter, ManagedMcpBinder),
                    "installed": find(adapter.executable) is not None,
                }
                for name in registry.list_supported_ids()
                if (adapter := registry.get(name)) is not None
            ]
        }

    def provider_setup(self, workspace: WorkspaceIdentity, provider: str) -> dict[str, Any]:
        find = provider_lookup(workspace.root, provider_path(workspace.root))
        report = DoctorService(BuiltinProviderDiscovery(which_fn=find)).run_diagnostics(
            [ProviderId(provider)]
        )
        result = report.providers[0]
        return {
            "installed": result.installed,
            "authentication": result.authentication_status.value,
        }

    def recover(self, workspace: WorkspaceIdentity, preview: bool) -> dict[str, Any]:
        report = RecoveryService().recover(workspace, dry_run=preview)
        return {
            "eligible": len(report.stale_session_ids),
            "reconciled": len(report.reconciled_session_ids),
            "uncertain": len(report.uncertain_session_ids),
        }

    @staticmethod
    def check_action(
        workspace: WorkspaceIdentity, action: AgentAction, *, probe: bool = True
    ) -> None:
        if probe and ProjectStatusService().workspace_activity(workspace) is not False:
            raise WorkspaceLockedError(lock_path=workspace.root / ".cortexshift/agent.lock")
        with read_workspace(workspace.root, expected_project_id=workspace.project.id) as (_, store):
            workspace.revalidate()
            if store.count_sessions_by_status(workspace.project.id, UNFINALIZED_SESSION_STATUSES):
                raise SessionRecoveryError("Review recovery for the unfinalized invocation first.")
            active = TaskService(store).require_active_task(workspace.project.id)
            if active.id != action.task_id:
                raise ActiveTaskChangedError("The active Task changed. Review your selection.")

    def plan(self, workspace: WorkspaceIdentity, action: AgentAction, path: str) -> AgentPlan:
        self.check_action(workspace, action)
        find = provider_lookup(workspace.root, path)
        if action.provider is not None:
            self._require_managed_provider(action.provider)
        if action.kind == "start":
            assert action.provider is not None
            StartService(which_fn=find).dry_run(action.provider, start_dir=workspace)
            return AgentPlan(provider=action.provider, mode="start")
        if action.kind == "switch":
            assert action.provider is not None
            switch = SwitchService(which_fn=find).dry_run(
                action.provider,
                action.session_id,
                start_dir=workspace,
                new_session=action.fresh_target,
            )
            return AgentPlan(
                provider=action.provider,
                mode="switch_native" if switch.selected_prior_target_session_id else "switch_fresh",
                source=switch.source_session_id,
                target=switch.selected_prior_target_session_id,
                bootstrap_turn=switch.bootstrap_model_turn_required,
            )
        plan = self._resume(ResumeService(which_fn=find), workspace, action, None, None)
        assert isinstance(plan, ResumeDryRunResult)
        self._require_managed_provider(plan.provider_id)
        return AgentPlan(
            provider=plan.provider_id,
            mode="native_resume" if plan.native_session_known else "task_continuation",
            source=plan.source_session_id,
        )

    @staticmethod
    def _require_managed_provider(provider: str) -> None:
        if not isinstance(ProviderRuntimeRegistry().get(provider), ManagedMcpBinder):
            raise UnsupportedDesktopProvider(
                "Select a provider that supports automatic Task context in Desktop."
            )

    @staticmethod
    def _resume(
        service: ResumeService,
        workspace: WorkspaceIdentity,
        action: AgentAction,
        plan: AgentPlan | None,
        notify: Callable[[str], None] | None,
    ) -> Session | ResumeDryRunResult:
        fresh = action.kind == "continue"
        expected = (plan.source, plan.mode) if plan and plan.source else None
        return service.resume(
            action.provider,
            None if fresh else action.session_id,
            workspace,
            dry_run=plan is None,
            new_session=fresh,
            continuation_session_id=action.session_id if fresh else None,
            expected_task_id=action.task_id,
            expected_plan=expected,
            on_prepared=(lambda session: notify(session.id)) if notify else None,
        )

    def execute(
        self,
        workspace: WorkspaceIdentity,
        action: AgentAction,
        plan: AgentPlan,
        path: str,
        prepared: Callable[[str, str], None],
    ) -> Session:
        if self.plan(workspace, action, path) != plan:
            raise ActiveTaskChangedError("The selected invocation changed. Review the new plan.")
        find = provider_lookup(workspace.root, path)
        session_id: str | None = None

        def record_preparation(identifier: str, phase: str) -> None:
            nonlocal session_id
            session_id = identifier
            prepared(identifier, phase)

        def record_spawn() -> None:
            if session_id is not None:
                prepared(session_id, "running")

        runner = SubprocessInteractiveProcessRunner(on_spawn=record_spawn)
        leases = DesktopLeaseManager(workspace, action)
        if action.kind == "start":
            return (
                StartService(which_fn=find, process_runner=runner)
                .start(
                    plan.provider,
                    task_id=action.task_id,
                    start_dir=workspace,
                    on_prepared=lambda session: record_preparation(session.id, "starting"),
                )
                .session
            )
        if action.kind == "switch":
            assert plan.source is not None
            return (
                SwitchService(which_fn=find, process_runner=runner)
                .switch(
                    plan.provider,
                    action.session_id,
                    start_dir=workspace,
                    new_session=action.fresh_target,
                    expected_task_id=action.task_id,
                    expected_selection=(plan.source, plan.target),
                    on_prepared=lambda handoff, _: record_preparation(
                        handoff.target_session_id or "", "context_prepared"
                    ),
                )
                .target_session
            )
        session = self._resume(
            ResumeService(which_fn=find, process_runner=runner, lease_manager=leases),
            workspace,
            action,
            plan,
            lambda identifier: record_preparation(identifier, "starting"),
        )
        assert isinstance(session, Session)
        return session
