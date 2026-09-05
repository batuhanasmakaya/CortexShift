"""Application service for orchestrating native provider launch and session lifecycle."""

import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from cortexshift.adapters.process_runner import SubprocessInteractiveProcessRunner
from cortexshift.adapters.providers.antigravity import AntigravityRuntimeAdapter
from cortexshift.adapters.providers.claude import ClaudeRuntimeAdapter
from cortexshift.adapters.providers.codex import CodexRuntimeAdapter
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.session_launcher import ProviderSessionLauncher
from cortexshift.domain.errors import (
    NoActiveTaskError,
    ProjectNotInitializedError,
    ProviderNotFoundError,
    TerminalRequiredError,
    UnknownProviderError,
    WorkspaceLockedError,
)
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.project import Project
from cortexshift.domain.provider import (
    ProviderId,
)
from cortexshift.domain.session import Session
from cortexshift.domain.task import Task
from cortexshift.ports.process_runner import InteractiveProcessRunner
from cortexshift.ports.provider import ProviderRuntimeAdapter
from cortexshift.ports.workspace_lease import WorkspaceLeaseManager


class DryRunResult(BaseModel):
    """Result of a dry-run provider launch preview."""

    model_config = ConfigDict(frozen=True)

    provider_id: ProviderId
    display_name: str
    executable: str
    project_id: str
    project_name: str
    task_id: str
    task_title: str
    cwd: Path
    argv: list[str]
    mode: str = "interactive"
    prompt_supplied: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize dry-run result without leaking raw prompts."""
        return {
            "provider_id": str(self.provider_id),
            "display_name": self.display_name,
            "executable": self.executable,
            "project_id": self.project_id,
            "project_name": self.project_name,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "cwd": str(self.cwd),
            "argv": self.argv,
            "mode": self.mode,
            "prompt_supplied": self.prompt_supplied,
        }


class ProviderRuntimeRegistry:
    """Registry of supported provider runtime adapters."""

    def __init__(self, adapters: list[ProviderRuntimeAdapter] | None = None) -> None:
        self._adapters: dict[str, ProviderRuntimeAdapter] = {}
        if adapters is None:
            adapters = [
                ClaudeRuntimeAdapter(),
                CodexRuntimeAdapter(),
                AntigravityRuntimeAdapter(),
            ]
        for adapter in adapters:
            self._adapters[str(adapter.provider_id).lower()] = adapter

    def get(self, provider_id_str: str) -> ProviderRuntimeAdapter | None:
        return self._adapters.get(provider_id_str.strip().lower())

    def list_supported_ids(self) -> list[str]:
        return sorted(self._adapters.keys())


class RunService:
    """Orchestrates native provider process launching and session lifecycle."""

    def __init__(
        self,
        registry: ProviderRuntimeRegistry | None = None,
        process_runner: InteractiveProcessRunner | None = None,
        lease_manager: WorkspaceLeaseManager | None = None,
        which_fn: Callable[[str], str | None] | None = None,
        is_tty_fn: Callable[[], bool] | None = None,
    ) -> None:
        self._registry = registry or ProviderRuntimeRegistry()
        self._runner = process_runner or SubprocessInteractiveProcessRunner()
        self._lease_manager = lease_manager or FileWorkspaceLeaseManager()
        self._which = which_fn if which_fn is not None else (lambda cmd: shutil.which(cmd))
        self._is_tty = is_tty_fn if is_tty_fn is not None else self._check_tty

    @staticmethod
    def _check_tty() -> bool:
        return sys.stdin.isatty() and sys.stdout.isatty()

    def _resolve_context(
        self,
        provider_name: str,
        start_dir: Path | str | None = None,
    ) -> tuple[Path, Project, Task, ProviderRuntimeAdapter, str, SQLiteStateStore]:
        """Resolve and validate project, active task, provider, and executable."""
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

            adapter = self._registry.get(provider_name)
            if adapter is None:
                raise UnknownProviderError(provider_name, self._registry.list_supported_ids())

            resolved_executable = self._which(adapter.executable)
            if not resolved_executable:
                raise ProviderNotFoundError(f"{adapter.display_name} was not found in PATH.")

            return project_root, project, task, adapter, resolved_executable, store
        except Exception:
            store.close()
            raise

    def dry_run(
        self,
        provider_name: str,
        prompt: str | None = None,
        start_dir: Path | str | None = None,
    ) -> DryRunResult:
        """Perform a dry-run preview of provider launch without side effects."""
        project_root, project, task, adapter, resolved_executable, store = self._resolve_context(
            provider_name, start_dir
        )
        try:
            launch_spec = adapter.build_launch_spec(
                project_root=Path(project.repo_path),
                executable_path=resolved_executable,
                prompt=prompt,
            )

            return DryRunResult(
                provider_id=adapter.provider_id,
                display_name=adapter.display_name,
                executable=resolved_executable,
                project_id=project.id,
                project_name=project.name,
                task_id=task.id,
                task_title=task.title,
                cwd=launch_spec.cwd,
                argv=launch_spec.to_redacted_argv(),
                mode="interactive",
                prompt_supplied=launch_spec.prompt_supplied,
            )
        finally:
            store.close()

    def run(
        self,
        provider_name: str,
        prompt: str | None = None,
        start_dir: Path | str | None = None,
        on_launch: Callable[[LaunchSpecification, Session, Task, Project], None] | None = None,
    ) -> Session:
        """Launch an interactive provider session and manage its lifecycle.

        Args:
            provider_name: Canonical identifier of provider (e.g. 'claude', 'codex', 'antigravity').
            prompt: Optional initial prompt.
            start_dir: Directory where the command was invoked.
            on_launch: Optional callback executed immediately before spawning process.

        Returns:
            The finalized Session entity.
        """
        project_root, project, task, adapter, resolved_executable, store = self._resolve_context(
            provider_name, start_dir
        )

        try:
            # 1. Check TTY requirement
            if not self._is_tty():
                raise TerminalRequiredError(
                    "Interactive provider launch requires a terminal (TTY).\n\n"
                    f"To simulate provider launch non-interactively, use: "
                    f"cortexshift run {provider_name} --dry-run"
                )

            # 2. Build launch specification (validates prompt capability before lease)
            launch_spec = adapter.build_launch_spec(
                project_root=Path(project.repo_path),
                executable_path=resolved_executable,
                prompt=prompt,
            )

            # 3. Acquire workspace lease
            lease = self._lease_manager.get_lease(Path(project.repo_path))
            if not lease.acquire():
                raise WorkspaceLockedError(lock_path=lease.lock_path)

            # 4. Delegate Session creation and lifecycle to the shared launcher
            launcher = ProviderSessionLauncher(process_runner=self._runner, store=store)
            try:
                session = launcher.start_session(
                    task_id=task.id,
                    provider_id=adapter.provider_id,
                    native_session_id=launch_spec.native_session_id,
                )

                def _forward(spec: LaunchSpecification, current: Session) -> None:
                    if on_launch:
                        on_launch(spec, current, task, project)

                return launcher.run(session, launch_spec, on_launch=_forward)
            finally:
                lease.release()
        finally:
            store.close()
