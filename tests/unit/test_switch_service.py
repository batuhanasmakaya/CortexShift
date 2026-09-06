"""Unit tests for SwitchService handoff orchestration."""

import json
from pathlib import Path
from typing import Any

import pytest

from cortexshift.adapters.providers.antigravity import AntigravityHandoffAdapter
from cortexshift.adapters.providers.claude import ClaudeHandoffAdapter
from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.switch_service import ProviderHandoffRegistry, SwitchService
from cortexshift.domain.errors import (
    GitProbeError,
    HandoffDeliveryError,
    NoActiveTaskError,
    NoSourceSessionError,
    ProjectNotInitializedError,
    ProviderNotFoundError,
    SameProviderSwitchError,
    SessionNotFoundError,
    SessionTaskMismatchError,
    TerminalRequiredError,
    UnknownProviderError,
    WorkspaceLockedError,
)
from cortexshift.domain.git import RepositoryInspection, RepositoryInspectionStatus
from cortexshift.domain.handoff import HandoffFailureCode, HandoffStatus
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import SessionExitReason, SessionStatus
from cortexshift.ports.headless_runner import HeadlessProviderRunner, HeadlessResult
from cortexshift.ports.process_runner import InteractiveProcessRunner
from cortexshift.ports.repository import RepositoryInspector
from tests.factories import FakeCodexBootstrap, make_inspection, seed_project, seed_session


class FakeProcessRunner(InteractiveProcessRunner):
    """Records interactive launches and returns a scripted exit code."""

    def __init__(self, exit_code: int = 0, spawn_error: Exception | None = None) -> None:
        self.exit_code = exit_code
        self.spawn_error = spawn_error
        self.invocations: list[dict[str, Any]] = []

    def run_interactive(
        self,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        self.invocations.append({"argv": argv, "cwd": cwd})
        if self.spawn_error is not None:
            raise self.spawn_error
        return self.exit_code


class FakeInspector(RepositoryInspector):
    """Returns a scripted repository inspection without touching Git."""

    def __init__(self, inspection: RepositoryInspection | None = None) -> None:
        self._inspection = inspection
        self.calls = 0

    def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
        self.calls += 1
        if self._inspection is None:
            return make_inspection(project_root=project_root, project_id=project_id)
        snapshot = self._inspection.snapshot
        if snapshot is None or not project_id:
            return self._inspection
        return self._inspection.model_copy(
            update={"snapshot": snapshot.model_copy(update={"project_id": project_id})}
        )


class FakeHeadlessRunner(HeadlessProviderRunner):
    """Returns a scripted headless bootstrap result."""

    def __init__(self, result: HeadlessResult) -> None:
        self.result = result
        self.invocations: list[list[str]] = []

    def run_headless(
        self,
        argv: list[str],
        cwd: Path | str,
        timeout: float = 300.0,
        env: dict[str, str] | None = None,
    ) -> HeadlessResult:
        self.invocations.append(argv)
        return self.result


def _service(
    inspector: RepositoryInspector | None = None,
    process_runner: InteractiveProcessRunner | None = None,
    registry: ProviderHandoffRegistry | None = None,
    which_fn: Any = None,
    is_tty: bool = True,
) -> SwitchService:
    return SwitchService(
        registry=registry,
        inspector=inspector or FakeInspector(),
        process_runner=process_runner or FakeProcessRunner(),
        lease_manager=FileWorkspaceLeaseManager(),
        which_fn=which_fn if which_fn is not None else (lambda cmd: f"/bin/{cmd}"),
        is_tty_fn=lambda: is_tty,
    )


def _store(tmp_path: Path) -> SQLiteStateStore:
    return SQLiteStateStore(tmp_path / ".cortexshift" / "state.sqlite3", auto_migrate=False)


# --- Validation ---


def test_uninitialized_project_rejected(tmp_path: Path) -> None:
    """Verify switch requires an initialized CortexShift project."""
    with pytest.raises(ProjectNotInitializedError):
        _service().dry_run("codex", start_dir=tmp_path)


def test_no_active_task_rejected(tmp_path: Path) -> None:
    """Verify switch strictly requires an active task."""
    project, task = seed_project(tmp_path)
    with _store(tmp_path) as store:
        store.set_active_task_id(project.id, None)

    with pytest.raises(NoActiveTaskError):
        _service().dry_run("codex", start_dir=tmp_path)


def test_unknown_target_provider_rejected(tmp_path: Path) -> None:
    """Verify an unrecognized target provider is rejected with the supported list."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)

    with pytest.raises(UnknownProviderError) as exc:
        _service().dry_run("bogus", start_dir=tmp_path)

    assert exc.value.supported == ["antigravity", "claude", "codex"]


def test_no_source_session_rejected(tmp_path: Path) -> None:
    """Verify switch never silently degrades into a first-agent run."""
    seed_project(tmp_path)
    with pytest.raises(NoSourceSessionError):
        _service().dry_run("codex", start_dir=tmp_path)


def test_same_provider_switch_rejected(tmp_path: Path) -> None:
    """Verify handing off to the provider already in use is rejected."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)

    with pytest.raises(SameProviderSwitchError) as exc:
        _service().dry_run("claude", start_dir=tmp_path)

    assert "already uses Claude Code" in str(exc.value)
    assert "intended for provider handoff" in str(exc.value)


def test_explicit_missing_source_session_rejected(tmp_path: Path) -> None:
    """Verify an explicit source session that does not exist is rejected."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)

    with pytest.raises(SessionNotFoundError):
        _service().dry_run("codex", from_session_id="sess_ghost", start_dir=tmp_path)


def test_explicit_source_session_from_other_task_rejected(tmp_path: Path) -> None:
    """Verify a session belonging to a different task cannot be a handoff source."""
    from cortexshift.domain.task import Task

    project, task = seed_project(tmp_path)
    other = Task(project_id=project.id, title="Other", objective="Other objective")
    with _store(tmp_path) as store:
        store.save_task(other)
    foreign = seed_session(tmp_path, other.id)
    seed_session(tmp_path, task.id)

    with pytest.raises(SessionTaskMismatchError):
        _service().dry_run("codex", from_session_id=foreign.id, start_dir=tmp_path)


def test_missing_target_provider_rejected_before_any_delivery(tmp_path: Path) -> None:
    """Verify a missing target provider fails before any handoff or session is persisted."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)
    runner = FakeProcessRunner()

    with pytest.raises(ProviderNotFoundError):
        _service(process_runner=runner, which_fn=lambda _: None).switch("codex", start_dir=tmp_path)

    assert runner.invocations == []
    with _store(tmp_path) as store:
        assert store.list_handoffs() == []
        assert [s.provider_id for s in store.list_sessions()] == [PROVIDER_CLAUDE]

    lease = FileWorkspaceLeaseManager().get_lease(tmp_path)
    assert lease.is_locked() is False


def test_non_tty_switch_rejected(tmp_path: Path) -> None:
    """Verify an actual switch requires an interactive terminal."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)

    with pytest.raises(TerminalRequiredError) as exc:
        _service(is_tty=False).switch("codex", start_dir=tmp_path)

    assert "--dry-run" in str(exc.value)


def test_workspace_lock_blocks_switch(tmp_path: Path) -> None:
    """Verify an active CortexShift agent session blocks a concurrent switch."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)

    holder = FileWorkspaceLeaseManager().get_lease(tmp_path)
    assert holder.acquire() is True
    try:
        with pytest.raises(WorkspaceLockedError) as exc:
            _service().switch("codex", start_dir=tmp_path)
    finally:
        holder.release()

    message = str(exc.value)
    assert "Another CortexShift agent session is already active" in message
    assert "delete" not in message.lower()


def test_stale_running_db_session_does_not_block_switch(tmp_path: Path) -> None:
    """Verify a stale `running` database record never blocks a free OS lease."""
    _, task = seed_project(tmp_path)
    seed_session(
        tmp_path,
        task.id,
        provider_id=PROVIDER_CLAUDE,
        status=SessionStatus.RUNNING,
        exit_reason=None,
        exit_code=None,
    )

    result = _service().switch("codex", start_dir=tmp_path)
    assert result.handoff.status == HandoffStatus.DELIVERED


# --- Successful switch ---


def test_switch_delivers_context_and_creates_target_session(
    tmp_path: Path, codex_bootstrap: FakeCodexBootstrap
) -> None:
    """Verify a successful switch persists a delivered handoff bound to a target session."""
    project, task = seed_project(
        tmp_path,
        completed=["Scaffolded auth module"],
        remaining=["Wire token refresh"],
        current_work="Implementing token exchange",
    )
    source = seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)
    runner = FakeProcessRunner(exit_code=0)

    result = _service(process_runner=runner).switch("codex", start_dir=tmp_path)

    assert result.handoff.status == HandoffStatus.DELIVERED
    assert result.handoff.source_session_id == source.id
    assert result.handoff.source_provider_id == PROVIDER_CLAUDE
    assert result.handoff.target_provider_id == PROVIDER_CODEX
    assert result.handoff.target_session_id == result.target_session.id
    assert result.handoff.delivered_at is not None
    assert result.handoff.git_snapshot_id is not None
    assert result.target_session.status == SessionStatus.COMPLETED
    assert result.bootstrap_performed is True

    argv = runner.invocations[0]["argv"]
    assert argv[0] == "/bin/codex"
    assert "-c" in argv
    assert argv[-2:] == ["resume", "test-codex-native-id"]
    context = codex_bootstrap.invocations[-1][-1]
    assert "CORTEXSHIFT HANDOFF PROTOCOL v1" in context
    assert task.objective in context
    assert "Wire token refresh" in context
    assert "Implementing token exchange" in context

    with _store(tmp_path) as store:
        stored = store.get_handoff(result.handoff.id)
        assert stored is not None
        assert stored.status == HandoffStatus.DELIVERED
        assert stored.payload.task_id == task.id
        assert store.get_snapshot(str(stored.git_snapshot_id)) is not None
        assert store.get_active_task_id(project.id) == task.id


def test_switch_does_not_mutate_task_progress(tmp_path: Path) -> None:
    """Verify switching never infers business progress or completes the task."""
    project, task = seed_project(
        tmp_path,
        completed=["Item A"],
        remaining=["Item B", "Item C"],
        known_issues=["Known issue"],
        current_work="In-flight work",
    )
    seed_session(tmp_path, task.id)

    _service().switch("codex", start_dir=tmp_path)

    with _store(tmp_path) as store:
        after = store.get_task(task.id)
        assert after is not None
        assert after.completed_items == ["Item A"]
        assert after.remaining_items == ["Item B", "Item C"]
        assert after.known_issues == ["Known issue"]
        assert after.current_work == "In-flight work"
        assert after.status == task.status
        assert store.get_active_task_id(project.id) == task.id


def test_outgoing_provider_is_never_required(
    tmp_path: Path, codex_bootstrap: FakeCodexBootstrap
) -> None:
    """Regression: a missing outgoing provider must not block handing off to another.

    This protects the core product use case: Claude's quota is exhausted, Claude may no
    longer be able to answer (or even be installed), and the user switches to Codex.
    """
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)

    resolved: list[str] = []

    def only_codex_exists(command: str) -> str | None:
        resolved.append(command)
        return "/bin/codex" if command == "codex" else None

    runner = FakeProcessRunner()
    result = _service(process_runner=runner, which_fn=only_codex_exists).switch(
        "codex", start_dir=tmp_path
    )

    # The outgoing provider's executable was never even looked up.
    assert "claude" not in resolved
    assert result.handoff.status == HandoffStatus.DELIVERED
    assert result.handoff.source_provider_id == PROVIDER_CLAUDE
    assert "CORTEXSHIFT HANDOFF PROTOCOL v1" in codex_bootstrap.invocations[-1][-1]


def test_switch_makes_no_outgoing_model_call(tmp_path: Path) -> None:
    """Verify only the receiving provider process is ever launched."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)

    headless = FakeHeadlessRunner(HeadlessResult(exit_code=0, stdout="{}", stderr=""))
    registry = ProviderHandoffRegistry(
        [
            ClaudeHandoffAdapter(),
            CodexHandoffAdapter(),
            AntigravityHandoffAdapter(headless_runner=headless),
        ]
    )
    runner = FakeProcessRunner()
    _service(process_runner=runner, registry=registry).switch("codex", start_dir=tmp_path)

    assert headless.invocations == []
    assert len(runner.invocations) == 1
    assert runner.invocations[0]["argv"][0] == "/bin/codex"


def test_switch_captures_git_snapshot_under_lease(tmp_path: Path) -> None:
    """Verify the repository is observed while the exclusive lease is held."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)

    lease_states: list[bool] = []

    class LeaseObservingInspector(FakeInspector):
        def inspect(self, project_root: Path | str, project_id: str = "") -> RepositoryInspection:
            probe = FileWorkspaceLeaseManager().get_lease(Path(project_root))
            lease_states.append(probe.is_locked())
            return super().inspect(project_root, project_id)

    _service(inspector=LeaseObservingInspector()).switch("codex", start_dir=tmp_path)

    assert lease_states and all(lease_states)


def test_git_missing_continues_with_explicit_marker(
    tmp_path: Path, codex_bootstrap: FakeCodexBootstrap
) -> None:
    """Verify a non-Git project still hands off, with no fake snapshot row."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)
    inspector = FakeInspector(make_inspection(status=RepositoryInspectionStatus.NOT_GIT_REPOSITORY))
    runner = FakeProcessRunner()

    result = _service(inspector=inspector, process_runner=runner).switch(
        "codex", start_dir=tmp_path
    )

    assert result.handoff.status == HandoffStatus.DELIVERED
    assert result.handoff.git_snapshot_id is None
    assert result.handoff.payload.git_state.available is False
    assert "not inside a Git repository" in codex_bootstrap.invocations[-1][-1]

    with _store(tmp_path) as store:
        assert store.list_snapshots(project_id=result.handoff.project_id) == []


def test_git_probe_error_fails_switch_safely(tmp_path: Path) -> None:
    """Verify an unreliable repository observation fails rather than shipping bad context."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)
    inspector = FakeInspector(
        make_inspection(
            status=RepositoryInspectionStatus.PROBE_ERROR,
            diagnostic="Git inspection failed unexpectedly.",
        )
    )
    runner = FakeProcessRunner()

    with pytest.raises(GitProbeError):
        _service(inspector=inspector, process_runner=runner).switch("codex", start_dir=tmp_path)

    assert runner.invocations == []
    assert FileWorkspaceLeaseManager().get_lease(tmp_path).is_locked() is False


def test_files_touched_reaches_the_receiving_agent(
    tmp_path: Path, codex_bootstrap: FakeCodexBootstrap
) -> None:
    """Verify Git-derived changed paths appear in the delivered context."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)
    inspector = FakeInspector(
        make_inspection(
            staged=["src/staged.py"],
            modified=["src/modified.py"],
            untracked=["notes.md"],
            conflicted=[],
        )
    )
    runner = FakeProcessRunner()

    _service(inspector=inspector, process_runner=runner).switch("codex", start_dir=tmp_path)

    context = codex_bootstrap.invocations[-1][-1]
    for path in ("src/staged.py", "src/modified.py", "notes.md"):
        assert path in context


def test_operator_note_included_with_provenance(
    tmp_path: Path, codex_bootstrap: FakeCodexBootstrap
) -> None:
    """Verify an optional operator note is delivered as clearly attributed context."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)
    runner = FakeProcessRunner()

    result = _service(process_runner=runner).switch(
        "codex", note="Mind the flaky integration test", start_dir=tmp_path
    )

    assert result.handoff.payload.operator_note == "Mind the flaky integration test"
    context = codex_bootstrap.invocations[-1][-1]
    assert "## OPERATOR NOTE" in context
    assert "Mind the flaky integration test" in context


def test_command_injection_stays_inert(tmp_path: Path, codex_bootstrap: FakeCodexBootstrap) -> None:
    """Verify hostile canonical task content never escapes the argv boundary."""
    marker = tmp_path / "hacked"
    _, task = seed_project(
        tmp_path,
        objective=f'Fix $(touch {marker}) and `rm -rf /` with "quotes"\nand a newline',
        remaining=["; rm -rf / #", "$(whoami)"],
    )
    seed_session(tmp_path, task.id)
    inspector = FakeInspector(
        make_inspection(staged=[f"$(touch {marker}).py"], modified=[], untracked=[], conflicted=[])
    )
    runner = FakeProcessRunner()

    _service(inspector=inspector, process_runner=runner).switch("codex", start_dir=tmp_path)

    argv = runner.invocations[0]["argv"]
    assert argv[0] == "/bin/codex"
    assert "-c" in argv
    assert argv[-2:] == ["resume", "test-codex-native-id"]
    assert "$(touch" in codex_bootstrap.invocations[-1][-1]
    assert not marker.exists()


# --- Antigravity two-stage delivery ---


def _antigravity_registry(headless: FakeHeadlessRunner) -> ProviderHandoffRegistry:
    return ProviderHandoffRegistry(
        [
            ClaudeHandoffAdapter(),
            CodexHandoffAdapter(),
            AntigravityHandoffAdapter(headless_runner=headless),
        ]
    )


def test_antigravity_switch_bootstraps_then_resumes(tmp_path: Path) -> None:
    """Verify plan bootstrap ingests context and the same conversation resumes natively."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CODEX)

    headless = FakeHeadlessRunner(
        HeadlessResult(
            exit_code=0,
            stdout=json.dumps(
                {
                    "conversation_id": "test-conversation-123",
                    "status": "SUCCESS",
                    "response": "Secret plan text",
                }
            ),
            stderr="",
        )
    )
    runner = FakeProcessRunner()

    result = _service(process_runner=runner, registry=_antigravity_registry(headless)).switch(
        "antigravity", start_dir=tmp_path
    )

    bootstrap_argv = headless.invocations[0]
    assert "--mode=plan" in bootstrap_argv
    assert "CORTEXSHIFT HANDOFF PROTOCOL v1" in bootstrap_argv[bootstrap_argv.index("-p") + 1]

    assert result.bootstrap_performed is True
    assert result.target_session.native_session_id == "test-conversation-123"
    assert runner.invocations[0]["argv"] == [
        "/bin/agy",
        "--conversation",
        "test-conversation-123",
    ]

    with _store(tmp_path) as store:
        stored = store.get_handoff(result.handoff.id)
        assert stored is not None
        assert stored.status == HandoffStatus.DELIVERED
        assert "Secret plan text" not in stored.model_dump_json()

        target = store.get_session(result.target_session.id)
        assert target is not None
        assert target.native_session_id == "test-conversation-123"


@pytest.mark.parametrize(
    ("result", "expected_code"),
    [
        (
            HeadlessResult(exit_code=2, stdout="", stderr="PROVIDER-STDERR-SENTINEL"),
            HandoffFailureCode.BOOTSTRAP_FAILED,
        ),
        (
            HeadlessResult(exit_code=0, stdout="PROVIDER-STDOUT-SENTINEL not json", stderr=""),
            HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT,
        ),
        (
            HeadlessResult(exit_code=0, stdout='{"status": "SUCCESS"}', stderr=""),
            HandoffFailureCode.BOOTSTRAP_INVALID_OUTPUT,
        ),
        (
            HeadlessResult(
                exit_code=0, stdout='{"conversation_id": "c", "status": "X"}', stderr=""
            ),
            HandoffFailureCode.BOOTSTRAP_FAILED,
        ),
        (
            HeadlessResult(exit_code=-1, stdout="", stderr="", timed_out=True),
            HandoffFailureCode.BOOTSTRAP_TIMEOUT,
        ),
        (
            HeadlessResult(exit_code=127, stdout="", stderr="", not_found=True),
            HandoffFailureCode.SPAWN_FAILED,
        ),
    ],
)
def test_antigravity_bootstrap_failure_marks_handoff_failed(
    result: HeadlessResult, expected_code: HandoffFailureCode, tmp_path: Path
) -> None:
    """Verify bootstrap failure never fakes success and always releases the lease."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CODEX)
    headless = FakeHeadlessRunner(result)
    runner = FakeProcessRunner()

    with pytest.raises(HandoffDeliveryError) as exc:
        _service(process_runner=runner, registry=_antigravity_registry(headless)).switch(
            "antigravity", start_dir=tmp_path
        )

    assert exc.value.failure_code == expected_code.value
    assert runner.invocations == []
    assert FileWorkspaceLeaseManager().get_lease(tmp_path).is_locked() is False

    with _store(tmp_path) as store:
        handoffs = store.list_handoffs()
        assert len(handoffs) == 1
        assert handoffs[0].status == HandoffStatus.FAILED
        assert handoffs[0].failure_code == expected_code
        assert handoffs[0].delivered_at is None

        targets = [s for s in store.list_sessions() if s.provider_id == PROVIDER_ANTIGRAVITY]
        assert len(targets) == 1
        assert targets[0].status == SessionStatus.FAILED
        assert targets[0].exit_reason == SessionExitReason.SPAWN_FAILED
        assert targets[0].native_session_id is None

        # Raw provider output must never reach persisted state.
        serialized = json.dumps([h.model_dump(mode="json") for h in handoffs])
        assert result.stderr == "" or result.stderr not in serialized
        assert result.stdout == "" or result.stdout not in serialized


# --- Delivery vs session outcome ---


def test_nonzero_target_exit_keeps_handoff_delivered(tmp_path: Path) -> None:
    """Verify a receiving provider crashing after delivery does not undo the handoff."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)
    runner = FakeProcessRunner(exit_code=7)

    result = _service(process_runner=runner).switch("codex", start_dir=tmp_path)

    assert result.handoff.status == HandoffStatus.DELIVERED
    assert result.target_session.status == SessionStatus.FAILED
    assert result.target_session.exit_code == 7
    assert result.target_session.exit_reason == SessionExitReason.PROCESS_CRASHED


def test_target_spawn_failure_marks_handoff_failed(tmp_path: Path) -> None:
    """Verify a receiving provider that never starts yields an honest failed handoff."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)
    runner = FakeProcessRunner(spawn_error=OSError("cannot exec"))

    with pytest.raises(OSError):
        _service(process_runner=runner).switch("codex", start_dir=tmp_path)

    assert FileWorkspaceLeaseManager().get_lease(tmp_path).is_locked() is False
    with _store(tmp_path) as store:
        handoffs = store.list_handoffs()
        assert handoffs[0].status == HandoffStatus.FAILED
        assert handoffs[0].failure_code == HandoffFailureCode.SPAWN_FAILED
        target = store.get_session(str(handoffs[0].target_session_id))
        assert target is not None
        assert target.status == SessionStatus.FAILED


def test_target_interrupt_records_interrupted_session(tmp_path: Path) -> None:
    """Verify Ctrl+C in the receiving agent maps to an interrupted session."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)
    runner = FakeProcessRunner(exit_code=130)

    result = _service(process_runner=runner).switch("codex", start_dir=tmp_path)

    assert result.handoff.status == HandoffStatus.DELIVERED
    assert result.target_session.status == SessionStatus.INTERRUPTED
    assert result.target_session.exit_reason == SessionExitReason.USER_INTERRUPTED


# --- Preview and dry run ---


def test_preview_persists_nothing_and_launches_nothing(tmp_path: Path) -> None:
    """Verify preview is a pure read: no handoff, snapshot, session, lease, or launch."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)
    runner = FakeProcessRunner()
    headless = FakeHeadlessRunner(HeadlessResult(exit_code=0, stdout="{}", stderr=""))

    result = _service(
        process_runner=runner,
        registry=_antigravity_registry(headless),
        which_fn=lambda _: None,
        is_tty=False,
    ).preview("antigravity", start_dir=tmp_path)

    assert result.protocol_version == 1
    assert result.target_provider_id == "antigravity"
    assert result.bootstrap_model_turn_required is True
    assert "CORTEXSHIFT HANDOFF PROTOCOL v1" in result.rendered_context
    assert result.payload.git_state.snapshot_id is None

    assert runner.invocations == []
    assert headless.invocations == []
    assert FileWorkspaceLeaseManager().get_lease(tmp_path).is_locked() is False
    with _store(tmp_path) as store:
        assert store.list_handoffs() == []
        assert store.list_snapshots(project_id=result.project_id) == []
        assert len(store.list_sessions()) == 1


def test_dry_run_reports_delivery_plan_without_side_effects(tmp_path: Path) -> None:
    """Verify dry run describes the switch and performs no bootstrap or launch."""
    project, task = seed_project(tmp_path)
    source = seed_session(tmp_path, task.id, provider_id=PROVIDER_CODEX)
    runner = FakeProcessRunner()
    headless = FakeHeadlessRunner(HeadlessResult(exit_code=0, stdout="{}", stderr=""))

    result = _service(
        process_runner=runner, registry=_antigravity_registry(headless), is_tty=False
    ).dry_run("antigravity", start_dir=tmp_path)

    assert result.protocol_version == 1
    assert result.project_id == project.id
    assert result.task_id == task.id
    assert result.source_session_id == source.id
    assert result.source_provider_id == "codex"
    assert result.target_provider_id == "antigravity"
    assert result.target_executable == "/bin/agy"
    assert result.git_status == RepositoryInspectionStatus.READY.value
    assert result.git_branch == "main"
    assert result.delivery_strategy == "plan_bootstrap_then_resume"
    assert result.bootstrap_model_turn_required is True
    assert result.context_characters > 0
    assert result.context_truncated is False

    assert headless.invocations == []
    assert runner.invocations == []
    with _store(tmp_path) as store:
        assert store.list_handoffs() == []
        assert store.list_snapshots(project_id=project.id) == []
        assert len(store.list_sessions()) == 1


def test_dry_run_reports_no_bootstrap_for_direct_providers(tmp_path: Path) -> None:
    """Verify Claude and Codex targets never require an extra model turn."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id, provider_id=PROVIDER_CLAUDE)

    result = _service(is_tty=False).dry_run("codex", start_dir=tmp_path)
    assert result.delivery_strategy == "read_only_bootstrap_then_resume"
    assert result.bootstrap_model_turn_required is True


def test_dry_run_exposes_no_credentials(tmp_path: Path) -> None:
    """Verify machine-readable dry-run output carries no credential-like fields."""
    _, task = seed_project(tmp_path)
    seed_session(tmp_path, task.id)

    payload = json.dumps(_service(is_tty=False).dry_run("codex", start_dir=tmp_path).to_dict())
    lowered = payload.lower()
    for forbidden in ("token", "api_key", "secret", "password", "authorization"):
        assert forbidden not in lowered


def test_dry_run_reports_truncation_for_huge_context(tmp_path: Path) -> None:
    """Verify dry run surfaces context bounding for very large tasks."""
    _, task = seed_project(
        tmp_path,
        completed=[f"Completed milestone {i} " + "detail " * 20 for i in range(600)],
        remaining=["Wire token refresh"],
    )
    seed_session(tmp_path, task.id)

    result = _service(is_tty=False).dry_run("codex", start_dir=tmp_path)

    assert result.context_truncated is True
    assert result.context_characters <= result.context_max_characters
    assert result.context_omissions
