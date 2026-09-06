"""Exact native resume without creating a handoff or repository snapshot."""

import sys
from pathlib import Path

from cortexshift.adapters.providers.antigravity import (
    ANTIGRAVITY_MCP_MISSING_NOTICE,
    is_antigravity_mcp_configured,
)
from cortexshift.application.native_session import select_native_session
from cortexshift.application.run_service import DryRunResult, RunService
from cortexshift.application.session_launcher import ProviderSessionLauncher
from cortexshift.domain.errors import NativeResumeError, TerminalRequiredError, WorkspaceLockedError
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY
from cortexshift.domain.session import Session
from cortexshift.ports.native_session import ProviderNativeSessionAdapter


class ResumeDryRunResult(DryRunResult):
    source_session_id: str
    native_session_id: str
    native_session_known: bool = True
    resume_strategy: str = "exact_native_id"
    bootstrap_model_turn_required: bool = False

    def to_dict(self) -> dict[str, object]:
        return self.model_dump(mode="json")


class ResumeService(RunService):
    """Share run context resolution and injected execution boundaries; never nest leases."""

    def resume(
        self,
        provider_name: str,
        session_id: str | None = None,
        start_dir: Path | str | None = None,
        *,
        dry_run: bool = False,
    ) -> Session | ResumeDryRunResult:
        _, project, task, adapter, executable, store = self._resolve_context(
            provider_name, start_dir
        )
        try:
            if not isinstance(adapter, ProviderNativeSessionAdapter):
                raise NativeResumeError("This provider does not support exact native resume.")

            def select() -> Session:
                source = select_native_session(
                    store,
                    task.id,
                    adapter.provider_id,
                    adapter.get_native_capabilities(),
                    session_id,
                )
                if source is None:
                    raise NativeResumeError(
                        f"No exact resumable {adapter.display_name} native session is recorded "
                        "for the active task. CortexShift will not guess a provider session. "
                        "Start a new native session with run, or switch to this provider instead."
                    )
                return source

            source = select()
            assert source.native_session_id is not None
            spec = adapter.build_exact_resume(
                Path(project.repo_path), executable, source.native_session_id
            )
            if dry_run:
                return ResumeDryRunResult(
                    provider_id=adapter.provider_id,
                    display_name=adapter.display_name,
                    executable=executable,
                    project_id=project.id,
                    project_name=project.name,
                    task_id=task.id,
                    task_title=task.title,
                    cwd=spec.cwd,
                    argv=spec.to_redacted_argv(),
                    mode="native_resume",
                    source_session_id=source.id,
                    native_session_id=source.native_session_id,
                )
            if not self._is_tty():
                raise TerminalRequiredError(
                    "Interactive resume requires a terminal (TTY). "
                    f"Use cortexshift resume {provider_name} --dry-run to preview."
                )
            lease = self._lease_manager.get_lease(Path(project.repo_path))
            if not lease.acquire():
                raise WorkspaceLockedError(lock_path=lease.lock_path)
            try:
                # Select again under the lease in case another invocation just finished.
                source = select()
                assert source.native_session_id is not None
                spec = adapter.build_exact_resume(
                    Path(project.repo_path), executable, source.native_session_id
                )
                launcher = ProviderSessionLauncher(
                    self._runner,
                    store,
                    checkpoint_service=self._checkpoint_service,
                )
                invocation = launcher.start_session(
                    task.id,
                    adapter.provider_id,
                    native_session_id=source.native_session_id,
                    resumed_from_session_id=source.id,
                )
                if str(adapter.provider_id) == str(
                    PROVIDER_ANTIGRAVITY
                ) and not is_antigravity_mcp_configured(Path(project.repo_path)):
                    sys.stderr.write(f"\n{ANTIGRAVITY_MCP_MISSING_NOTICE}\n\n")

                return launcher.run(invocation, spec)
            finally:
                lease.release()
        finally:
            store.close()
