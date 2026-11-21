"""Runs the dashboard, then hands the terminal to a native provider.

The ordering enforced here is an architectural guarantee, not a convenience:

    CortexShiftApp.run()   →   returns TuiExitRequest
                               (Textual has torn down and restored the terminal)
                           →   RunService / ResumeService / SwitchService
                           →   native provider owns the terminal

No provider process is ever started while the Textual application is alive, and no
provider TUI is embedded, captured, or multiplexed. `launch_provider` is only ever
reached after `run_dashboard` has returned.
"""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from cortexshift.application.handoff_renderer import RenderedHandoffContext
from cortexshift.application.resume_service import ResumeDryRunResult, ResumeService
from cortexshift.application.run_service import RunService
from cortexshift.application.switch_service import SwitchService
from cortexshift.application.workspace import WorkspaceIdentity, resolve_workspace
from cortexshift.domain.errors import ProjectConflictError, TerminalRequiredError
from cortexshift.domain.handoff import HandoffRecord
from cortexshift.domain.launch import LaunchSpecification
from cortexshift.domain.session import Session
from cortexshift.tui.actions import TuiExitAction, TuiExitRequest
from cortexshift.tui.app import CortexShiftApp, build_app

TTY_REQUIRED_MESSAGE = (
    "The CortexShift dashboard requires an interactive terminal (TTY).\n\n"
    "Both standard input and standard output must be attached to a terminal."
)


def _silent(message: str) -> None:
    """Default announcement sink: the dashboard says nothing on its own."""


def terminal_is_interactive() -> bool:
    """Whether both stdin and stdout are attached to a terminal."""
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:
        return False


@dataclass
class TuiSession:
    """Result of one dashboard invocation and any provider launch that followed."""

    request: TuiExitRequest | None = None
    session: Session | None = None
    launched: bool = False
    events: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        """Process exit code derived from the launched provider session, if any."""
        if self.session is None:
            return 0
        from cortexshift.domain.session import SessionStatus

        if self.session.status in (SessionStatus.COMPLETED, SessionStatus.INTERRUPTED):
            return 0
        return self.session.exit_code or 1


class TuiCoordinator:
    """Sequences dashboard lifetime and native provider launch."""

    def __init__(
        self,
        *,
        run_service: RunService | None = None,
        resume_service: ResumeService | None = None,
        switch_service: SwitchService | None = None,
        app_factory: Callable[[], CortexShiftApp] | None = None,
        dashboard_runner: Callable[[], TuiExitRequest | None] | None = None,
        is_tty: Callable[[], bool] = terminal_is_interactive,
        announce: Callable[[str], None] | None = None,
    ) -> None:
        self._run_service = run_service
        self._resume_service = resume_service
        self._switch_service = switch_service
        self._app_factory = app_factory
        self._dashboard_runner = dashboard_runner
        self._is_tty = is_tty
        self._announce = announce or _silent

    # ------------------------------------------------------------------

    def start(self, project_root: Path | str | None = None) -> TuiSession:
        """Run the dashboard and, if requested, launch a provider afterwards.

        Raises:
            ProjectNotInitializedError: If no initialized project is found.
            TerminalRequiredError: If the process has no interactive terminal.
        """
        result = TuiSession()

        workspace = (
            resolve_workspace(project_root)
            if project_root is not None or self._dashboard_runner is None
            else None
        )
        request = self._run_dashboard(workspace)
        result.events.append("dashboard_finished")
        result.request = request

        if request is None:
            return result

        self._announce(f"{request.description} — CortexShift released the terminal.")
        result.events.append("provider_launch_started")
        result.launched = True
        result.session = self.launch_provider(request, workspace=workspace)
        result.events.append("provider_launch_finished")
        return result

    def _run_dashboard(self, workspace: WorkspaceIdentity | None) -> TuiExitRequest | None:
        """Run the Textual application to completion and return its exit request.

        The project is resolved before the terminal is checked: someone standing in the
        wrong directory should be told that, not told about their terminal.
        """
        if self._dashboard_runner is not None:
            return self._dashboard_runner()

        if not self._is_tty():
            raise TerminalRequiredError(TTY_REQUIRED_MESSAGE)

        app = self._app_factory() if self._app_factory else build_app(workspace)
        return app.run()

    # ------------------------------------------------------------------

    def launch_provider(
        self, request: TuiExitRequest, *, workspace: WorkspaceIdentity | None = None
    ) -> Session | None:
        """Invoke the existing provider service for a dashboard exit request.

        Only ever called after the Textual application has finished. The provider owns
        the terminal from here; CortexShift does not wrap, capture, or emulate it.
        """
        if workspace is not None and request.workspace is not None:
            workspace.revalidate()
            request.workspace.revalidate()
            if (
                not workspace.root.samefile(request.workspace.root)
                or workspace.project.id != request.workspace.project.id
            ):
                raise ProjectConflictError(
                    "Workspace identity mismatch between the dashboard and its provider request."
                )
        workspace = request.workspace or workspace
        if workspace is None:
            raise ProjectConflictError(
                "Workspace identity mismatch. The dashboard returned no verified workspace."
            )
        workspace.revalidate()

        if request.action is TuiExitAction.RUN:
            service = self._run_service or RunService()
            return service.run(provider_name=request.provider, start_dir=workspace)

        if request.action is TuiExitAction.RESUME:
            service_resume = self._resume_service or ResumeService()
            outcome = service_resume.resume(
                provider_name=request.provider,
                session_id=request.selected_session_id,
                start_dir=workspace,
                on_launch=self._announce_resume,
            )
            return outcome if isinstance(outcome, Session) else None

        service_switch = self._switch_service or SwitchService()
        switch_result = service_switch.switch(
            target_provider_name=request.provider,
            new_session=request.force_new_session,
            resume_session_id=request.selected_session_id,
            note=request.note,
            start_dir=workspace,
            on_prepared=self._announce_handoff,
            on_launch=self._announce_switch,
        )
        return switch_result.target_session

    def _announce_resume(self, plan: ResumeDryRunResult) -> None:
        self._announce(f"Requesting exact native resume in {plan.display_name}.")
        if not plan.automatic_context:
            self._announce("Automatic Task refresh is unavailable for this resume.")

    def _announce_switch(self, spec: LaunchSpecification, session: Session) -> None:
        if session.resumed_from_session_id:
            self._announce(f"Requesting exact native resume in {spec.provider_id}.")
        else:
            self._announce(f"Opening a fresh conversation in {spec.provider_id} on the same Task.")

    def _announce_handoff(self, record: HandoffRecord, rendered: RenderedHandoffContext) -> None:
        """Expose the historical fallback after the dashboard releases the terminal."""
        if rendered.omissions:
            labels = ", ".join(
                f"{item.section.replace('_', ' ')} ({item.omitted_items})"
                for item in rendered.omissions
            )
            self._announce(f"Handoff excerpt omitted {labels}.")
        self._announce(f"Full historical handoff: cortexshift handoff show {record.id} --json")


def resolve_project_root(start_dir: Path | str | None = None) -> Path:
    """Resolve the initialized project the dashboard should bind to.

    Raises:
        ProjectNotInitializedError: If no initialized project root is found.
    """
    return resolve_workspace(start_dir).root
