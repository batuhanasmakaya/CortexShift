"""The coordinator launches a provider only after the dashboard has finished.

These tests run entirely outside Textual: the guarantee under test is call ordering, not
rendering. No real provider process is ever started.
"""

from pathlib import Path

import pytest

from cortexshift.domain.errors import ProjectNotInitializedError, TerminalRequiredError
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX, ProviderId
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from cortexshift.tui.actions import TuiExitAction, TuiExitRequest
from cortexshift.tui.coordinator import TuiCoordinator, resolve_project_root


class RecordingProviderServices:
    """Fake run/resume/switch services that record when they were called."""

    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline
        self.calls: list[tuple[str, dict[str, object]]] = []

    def _session(self, provider: str) -> Session:
        return Session(
            task_id="task_x",
            provider_id=ProviderId(provider),
            status=SessionStatus.COMPLETED,
            ended_at=utc_now(),
            exit_reason=SessionExitReason.NORMAL_COMPLETION,
            exit_code=0,
        )

    # RunService surface
    def run(self, provider_name: str, **kwargs: object) -> Session:
        self.timeline.append("provider_started")
        self.calls.append(("run", {"provider_name": provider_name, **kwargs}))
        return self._session(provider_name)

    # ResumeService surface
    def resume(self, provider_name: str, session_id: str | None = None, **kw: object) -> Session:
        self.timeline.append("provider_started")
        self.calls.append(("resume", {"provider_name": provider_name, "session_id": session_id}))
        return self._session(provider_name)

    # SwitchService surface
    def switch(self, target_provider_name: str, **kwargs: object) -> object:
        self.timeline.append("provider_started")
        self.calls.append(("switch", {"target_provider_name": target_provider_name, **kwargs}))

        class Result:
            target_session = self._session(target_provider_name)

        return Result()


def build_coordinator(
    timeline: list[str],
    request: TuiExitRequest | None,
    services: RecordingProviderServices,
) -> TuiCoordinator:
    """Build a coordinator whose dashboard is a recorded fake."""

    def dashboard() -> TuiExitRequest | None:
        timeline.append("dashboard_started")
        timeline.append("dashboard_returned")
        return request

    return TuiCoordinator(
        run_service=services,  # type: ignore[arg-type]
        resume_service=services,  # type: ignore[arg-type]
        switch_service=services,  # type: ignore[arg-type]
        dashboard_runner=dashboard,
    )


def test_switch_is_invoked_only_after_the_dashboard_returns() -> None:
    timeline: list[str] = []
    services = RecordingProviderServices(timeline)
    request = TuiExitRequest(action=TuiExitAction.SWITCH, provider="codex")

    result = build_coordinator(timeline, request, services).start()

    assert timeline == ["dashboard_started", "dashboard_returned", "provider_started"]
    assert timeline.index("dashboard_returned") < timeline.index("provider_started")
    assert services.calls[0][0] == "switch"
    assert services.calls[0][1]["target_provider_name"] == "codex"
    assert result.launched is True
    assert result.session is not None


def test_run_is_invoked_only_after_the_dashboard_returns() -> None:
    timeline: list[str] = []
    services = RecordingProviderServices(timeline)
    request = TuiExitRequest(action=TuiExitAction.RUN, provider="claude")

    build_coordinator(timeline, request, services).start()

    assert timeline.index("dashboard_returned") < timeline.index("provider_started")
    assert services.calls[0][0] == "run"


def test_resume_forwards_the_selected_session() -> None:
    timeline: list[str] = []
    services = RecordingProviderServices(timeline)
    request = TuiExitRequest(
        action=TuiExitAction.RESUME, provider="codex", selected_session_id="sess_abc"
    )

    build_coordinator(timeline, request, services).start()

    assert services.calls[0][0] == "resume"
    assert services.calls[0][1]["session_id"] == "sess_abc"


def test_no_provider_runs_when_the_dashboard_returns_nothing() -> None:
    timeline: list[str] = []
    services = RecordingProviderServices(timeline)

    result = build_coordinator(timeline, None, services).start()

    assert timeline == ["dashboard_started", "dashboard_returned"]
    assert services.calls == []
    assert result.launched is False
    assert result.session is None
    assert result.exit_code == 0


def test_events_record_the_handoff_ordering() -> None:
    timeline: list[str] = []
    services = RecordingProviderServices(timeline)
    request = TuiExitRequest(action=TuiExitAction.SWITCH, provider="codex")

    result = build_coordinator(timeline, request, services).start()

    assert result.events == [
        "dashboard_finished",
        "provider_launch_started",
        "provider_launch_finished",
    ]
    assert result.events.index("dashboard_finished") < result.events.index(
        "provider_launch_started"
    )


def test_switch_forwards_new_session_and_note() -> None:
    timeline: list[str] = []
    services = RecordingProviderServices(timeline)
    request = TuiExitRequest(
        action=TuiExitAction.SWITCH,
        provider="antigravity",
        force_new_session=True,
        note="Quota exhausted",
    )

    build_coordinator(timeline, request, services).start()

    _, kwargs = services.calls[0]
    assert kwargs["new_session"] is True
    assert kwargs["note"] == "Quota exhausted"
    assert kwargs["resume_session_id"] is None


def test_a_failed_provider_session_surfaces_as_a_non_zero_exit_code() -> None:
    timeline: list[str] = []

    class FailingServices(RecordingProviderServices):
        def _session(self, provider: str) -> Session:
            return Session(
                task_id="task_x",
                provider_id=ProviderId(provider),
                status=SessionStatus.FAILED,
                ended_at=utc_now(),
                exit_reason=SessionExitReason.PROCESS_CRASHED,
                exit_code=42,
            )

    services = FailingServices(timeline)
    request = TuiExitRequest(action=TuiExitAction.RUN, provider="claude")

    result = build_coordinator(timeline, request, services).start()

    assert result.exit_code == 42


def test_an_interrupted_session_is_not_an_error() -> None:
    timeline: list[str] = []

    class InterruptedServices(RecordingProviderServices):
        def _session(self, provider: str) -> Session:
            return Session(
                task_id="task_x",
                provider_id=ProviderId(provider),
                status=SessionStatus.INTERRUPTED,
                exit_reason=SessionExitReason.USER_INTERRUPTED,
                exit_code=130,
            )

    services = InterruptedServices(timeline)
    request = TuiExitRequest(action=TuiExitAction.RUN, provider="claude")

    assert build_coordinator(timeline, request, services).start().exit_code == 0


def test_the_dashboard_refuses_to_start_without_a_terminal(tmp_path: Path) -> None:
    """The real dashboard requires a TTY; headless tests bypass it deliberately."""
    from cortexshift.application.init_service import ProjectInitializationService

    ProjectInitializationService().initialize(tmp_path, name="Headless")
    coordinator = TuiCoordinator(is_tty=lambda: False)

    with pytest.raises(TerminalRequiredError) as excinfo:
        coordinator.start(tmp_path)

    assert "interactive terminal" in str(excinfo.value)


def test_an_uninitialized_directory_is_reported_before_the_terminal_check(
    tmp_path: Path,
) -> None:
    """Being in the wrong directory is the more useful thing to say."""
    coordinator = TuiCoordinator(is_tty=lambda: False)

    with pytest.raises(ProjectNotInitializedError):
        coordinator.start(tmp_path)


def test_resolving_an_uninitialized_directory_fails_cleanly(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotInitializedError):
        resolve_project_root(tmp_path)


def test_exit_request_describes_itself_for_operator_output() -> None:
    assert (
        TuiExitRequest(action=TuiExitAction.SWITCH, provider=str(PROVIDER_CODEX)).description
        == "Switch to codex"
    )
    assert (
        TuiExitRequest(action=TuiExitAction.RUN, provider=str(PROVIDER_CLAUDE)).description
        == "Run claude"
    )
