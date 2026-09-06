"""Exact identity, selection, dry-run, and failure regression coverage."""

import importlib
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from typer.testing import CliRunner

from cortexshift.adapters.providers.antigravity import (
    AntigravityHandoffAdapter,
    AntigravityRuntimeAdapter,
)
from cortexshift.adapters.providers.claude import ClaudeRuntimeAdapter, build_claude_mcp_config
from cortexshift.adapters.providers.codex import (
    CodexHandoffAdapter,
    CodexRuntimeAdapter,
    build_codex_mcp_args,
)
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager
from cortexshift.application.native_session import (
    is_native_resumable,
    native_capabilities,
    select_native_session,
)
from cortexshift.application.resume_service import ResumeDryRunResult, ResumeService
from cortexshift.application.run_service import RunService
from cortexshift.application.switch_service import ProviderHandoffRegistry, SwitchService
from cortexshift.cli.app import app
from cortexshift.domain.errors import (
    HandoffDeliveryError,
    NativeResumeError,
    SessionNotFoundError,
    SessionTaskMismatchError,
    TerminalRequiredError,
    WorkspaceLockedError,
)
from cortexshift.domain.identifiers import utc_now
from cortexshift.domain.provider import PROVIDER_CLAUDE, PROVIDER_CODEX, ProviderId
from cortexshift.domain.session import Session, SessionExitReason
from cortexshift.domain.task import Task
from cortexshift.ports.headless_runner import HeadlessResult
from tests.factories import FakeCodexBootstrap, patch_which, seed_project, seed_session
from tests.unit.test_switch_service import FakeHeadlessRunner, FakeInspector, FakeProcessRunner


def store_at(root: Path) -> SQLiteStateStore:
    return SQLiteStateStore(root / ".cortexshift/state.sqlite3", auto_migrate=False)


def known(root: Path, task_id: str, provider: str = "claude", native: str = "native-A") -> Session:
    session = seed_session(root, task_id, provider_id=ProviderId(provider))
    session = session.model_copy(update={"native_session_id": native})
    with store_at(root) as store:
        store.save_session(session)
    return session


def resume_service(runner: FakeProcessRunner | None = None, tty: bool = True) -> ResumeService:
    return ResumeService(
        process_runner=runner or FakeProcessRunner(),
        which_fn=lambda cmd: "/fake/" + cmd,
        is_tty_fn=lambda: tty,
    )


@pytest.mark.parametrize(
    ("adapter", "expected_suffix"),
    [
        (
            ClaudeRuntimeAdapter(),
            ["--mcp-config", build_claude_mcp_config(), "--resume", "native-A"],
        ),
        (CodexRuntimeAdapter(), [*build_codex_mcp_args(), "resume", "native-A"]),
        (AntigravityRuntimeAdapter(), ["--conversation", "native-A"]),
    ],
)
def test_exact_resume_contract(adapter, expected_suffix, tmp_path: Path) -> None:
    spec = adapter.build_exact_resume(tmp_path, "/fake/tool", "native-A")
    assert spec.argv == ["/fake/tool", *expected_suffix]
    assert spec.cwd == tmp_path
    assert spec.native_session_id == "native-A"
    assert spec.to_redacted_argv() == spec.argv
    assert adapter.get_native_capabilities().supports_exact_resume
    with pytest.raises(NativeResumeError):
        adapter.build_exact_resume(tmp_path, "/fake/tool", "--last")


def test_claude_uuid_persisted_before_launch_and_prompt_not_persisted(tmp_path: Path) -> None:
    _, task = seed_project(tmp_path)
    runner = FakeProcessRunner()
    service = RunService(
        process_runner=runner, which_fn=lambda cmd: "/fake/" + cmd, is_tty_fn=lambda: True
    )

    def inspect_before(spec, session, task, project):
        assert UUID(session.native_session_id).version == 4
        with store_at(tmp_path) as store:
            stored = store.get_session(session.id)
            assert stored is not None
            assert stored.native_session_id == session.native_session_id

    session = service.run(
        "claude", prompt="SECRET-USER-PROMPT", start_dir=tmp_path, on_launch=inspect_before
    )
    assert runner.invocations[0]["argv"] == [
        "/fake/claude",
        "--mcp-config",
        build_claude_mcp_config(),
        "--session-id",
        session.native_session_id,
        "SECRET-USER-PROMPT",
    ]
    with store_at(tmp_path) as store:
        assert "SECRET-USER-PROMPT" not in str(store.list_sessions())
    assert session.task_id == task.id


@pytest.mark.parametrize("provider", ["codex", "antigravity"])
def test_plain_run_keeps_unknown_id_without_model_turn(
    tmp_path: Path, provider: str, codex_bootstrap: FakeCodexBootstrap
) -> None:
    seed_project(tmp_path)
    process = FakeProcessRunner()
    service = RunService(
        process_runner=process, which_fn=lambda cmd: "/fake/" + cmd, is_tty_fn=lambda: True
    )
    session = service.run(provider, start_dir=tmp_path)
    assert session.native_session_id is None
    expected_len = 1 + len(build_codex_mcp_args()) if provider == "codex" else 1
    assert len(process.invocations[0]["argv"]) == expected_len
    assert not codex_bootstrap.invocations
    with pytest.raises(NativeResumeError, match="will not guess"):
        resume_service().resume(provider, start_dir=tmp_path)


def test_selection_skips_unknown_spawn_failed_other_tasks_and_old_cutoff(tmp_path: Path) -> None:
    _, task = seed_project(tmp_path)
    first = known(tmp_path, task.id)
    with store_at(tmp_path) as store:
        for n in range(105):
            store.save_session(
                Session(
                    task_id=task.id,
                    provider_id=PROVIDER_CLAUDE,
                    started_at=utc_now() + timedelta(seconds=n),
                    native_session_id="bad" if n % 2 else None,
                    exit_code=1,
                    exit_reason=SessionExitReason.SPAWN_FAILED,
                )
            )
        caps = ClaudeRuntimeAdapter().get_native_capabilities()
        assert select_native_session(store, task.id, PROVIDER_CLAUDE, caps) == first
        latest = first.model_copy(
            update={"id": "sess_latest", "started_at": utc_now() + timedelta(days=1)}
        )
        store.save_session(latest)
        assert select_native_session(store, task.id, PROVIDER_CLAUDE, caps) == latest
        other = Task(project_id=task.project_id, title="Other", objective="Other")
        store.save_task(other)
        foreign = latest.model_copy(update={"id": "sess_other", "task_id": other.id})
        store.save_session(foreign)
        assert select_native_session(store, task.id, PROVIDER_CLAUDE, caps) == latest
        with pytest.raises(SessionTaskMismatchError):
            select_native_session(store, task.id, PROVIDER_CLAUDE, caps, foreign.id)
        with pytest.raises(SessionNotFoundError):
            select_native_session(store, task.id, PROVIDER_CLAUDE, caps, "missing")
        with pytest.raises(NativeResumeError, match="another provider"):
            select_native_session(store, task.id, PROVIDER_CODEX, caps, first.id)
        assert not is_native_resumable(first, native_capabilities(object()))


@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity"])
def test_resume_lineage_restart_and_dry_run_zero_effects(
    tmp_path: Path, provider: str, codex_bootstrap: FakeCodexBootstrap
) -> None:
    project, task = seed_project(tmp_path)
    first = known(tmp_path, task.id, provider)
    service = resume_service()
    dry = service.resume(provider, start_dir=tmp_path, dry_run=True)
    assert isinstance(dry, ResumeDryRunResult)
    assert dry.source_session_id == first.id and dry.native_session_known
    assert not (tmp_path / ".cortexshift/agent.lock").exists()
    with store_at(tmp_path) as store:
        assert len(store.list_sessions()) == 1
        assert not store.list_handoffs(project_id=project.id)
        assert not store.list_snapshots(project.id)
    second = service.resume(provider, start_dir=tmp_path)
    third = resume_service().resume(provider, start_dir=tmp_path)
    assert isinstance(second, Session) and isinstance(third, Session)
    assert second.resumed_from_session_id == first.id
    assert third.resumed_from_session_id == second.id
    assert len({first.id, second.id, third.id}) == 3
    with store_at(tmp_path) as store:
        assert store.get_session(first.id) == first
        assert store.get_session(second.id) == second
        assert store.get_session(third.id) == third
        assert {s.native_session_id for s in store.list_sessions()} == {"native-A"}
        assert not store.list_handoffs(project_id=project.id)
        assert not store.list_snapshots(project.id)
        assert store.get_task(task.id) == task
    assert not codex_bootstrap.invocations


@pytest.mark.parametrize("exit_code", [1, 42, 130])
def test_resume_provider_failure_has_no_fallback(tmp_path: Path, exit_code: int) -> None:
    _, task = seed_project(tmp_path)
    first = known(tmp_path, task.id)
    process = FakeProcessRunner(exit_code=exit_code)
    result = resume_service(process).resume("claude", start_dir=tmp_path)
    assert isinstance(result, Session)
    assert result.exit_code == exit_code and result.resumed_from_session_id == first.id
    assert len(process.invocations) == 1
    assert not FileWorkspaceLeaseManager().get_lease(tmp_path).is_locked()
    with store_at(tmp_path) as store:
        assert store.get_session(first.id) == first


def test_resume_lock_tty_spawn_failure_and_explicit_selection(tmp_path: Path) -> None:
    _, task = seed_project(tmp_path)
    first = known(tmp_path, task.id)
    second = known(tmp_path, task.id, native="native-B")
    with pytest.raises(TerminalRequiredError):
        resume_service(tty=False).resume("claude", start_dir=tmp_path)
    lock = FileWorkspaceLeaseManager().get_lease(tmp_path)
    assert lock.acquire()
    try:
        with pytest.raises(WorkspaceLockedError):
            resume_service().resume("claude", start_dir=tmp_path)
        # Dry run is still possible while another process holds the workspace lease.
        assert isinstance(
            resume_service().resume("claude", start_dir=tmp_path, dry_run=True), ResumeDryRunResult
        )
    finally:
        lock.release()
    with pytest.raises(OSError):
        resume_service(FakeProcessRunner(spawn_error=OSError("cannot spawn"))).resume(
            "claude", start_dir=tmp_path
        )
    assert not lock.is_locked()
    with store_at(tmp_path) as store:
        failed = store.list_sessions()[0]
        assert failed.exit_reason == SessionExitReason.SPAWN_FAILED
        assert not is_native_resumable(failed, ClaudeRuntimeAdapter().get_native_capabilities())
        assert store.get_session(second.id) == second
    result = resume_service().resume("claude", first.id, start_dir=tmp_path)
    assert isinstance(result, Session) and result.native_session_id == first.native_session_id


@pytest.mark.parametrize(
    "result",
    [
        HeadlessResult(exit_code=0, stdout="not json", stderr="PRIVATE"),
        HeadlessResult(exit_code=0, stdout="{}", stderr="PRIVATE"),
        HeadlessResult(
            exit_code=0, stdout='{"type":"thread.started","thread_id":""}', stderr="PRIVATE"
        ),
        HeadlessResult(
            exit_code=0, stdout='{"type":"thread.started","thread_id":"--last"}', stderr="PRIVATE"
        ),
        HeadlessResult(exit_code=0, stdout='{"type":"turn.failed"}', stderr="PRIVATE"),
        HeadlessResult(
            exit_code=0, stdout='{"type":"thread.started","thread_id":"A"}', stderr="PRIVATE"
        ),
        HeadlessResult(exit_code=1, stdout="PRIVATE", stderr="PRIVATE"),
        HeadlessResult(exit_code=-1, stdout="PRIVATE", stderr="PRIVATE", timed_out=True),
        HeadlessResult(exit_code=127, stdout="PRIVATE", stderr="PRIVATE", not_found=True),
    ],
)
@pytest.mark.parametrize("existing", [False, True])
def test_codex_bootstrap_failure_preserves_old_state(
    tmp_path: Path, result: HeadlessResult, existing: bool
) -> None:
    project, task = seed_project(tmp_path)
    old = known(tmp_path, task.id, "codex", "native-B") if existing else None
    seed_session(tmp_path, task.id)
    process = FakeProcessRunner()
    headless = FakeHeadlessRunner(result)
    service = SwitchService(
        registry=ProviderHandoffRegistry([CodexHandoffAdapter(headless_runner=headless)]),
        inspector=FakeInspector(),
        process_runner=process,
        which_fn=lambda cmd: "/fake/" + cmd,
        is_tty_fn=lambda: True,
    )
    with pytest.raises(HandoffDeliveryError) as err:
        service.switch("codex", start_dir=tmp_path)
    assert "PRIVATE" not in str(err.value)
    assert not process.invocations
    assert len(headless.invocations) == 1
    assert not FileWorkspaceLeaseManager().get_lease(tmp_path).is_locked()
    with store_at(tmp_path) as store:
        if old:
            assert store.get_session(old.id) == old
        failed = store.list_sessions()[0]
        assert not is_native_resumable(failed, CodexRuntimeAdapter().get_native_capabilities())
        handoff = store.list_handoffs(project_id=project.id)[0]
        assert handoff.status == "failed" and handoff.target_session_id == failed.id
        assert "PRIVATE" not in str(store.list_sessions()) + str(handoff)


@pytest.mark.parametrize("provider", ["codex", "antigravity"])
def test_bootstrap_mismatched_id_never_resumes_another_conversation(
    tmp_path: Path, provider: str
) -> None:
    stdout = (
        '{"type":"thread.started","thread_id":"different"}\n{"type":"turn.completed"}'
        if provider == "codex"
        else '{"conversation_id":"different","status":"SUCCESS"}'
    )
    fake = FakeHeadlessRunner(HeadlessResult(exit_code=0, stdout=stdout, stderr=""))
    adapter = (
        CodexHandoffAdapter(headless_runner=fake)
        if provider == "codex"
        else AntigravityHandoffAdapter(headless_runner=fake)
    )
    with pytest.raises(HandoffDeliveryError, match="different"):
        adapter.prepare_delivery("/fake/tool", tmp_path, "fresh", native_session_id="existing")


def test_resume_cli_json_and_history(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, task = seed_project(tmp_path)
    first = known(tmp_path, task.id)
    monkeypatch.chdir(tmp_path)
    patch_which(monkeypatch, "cortexshift.application.run_service", lambda cmd: "/fake/" + cmd)
    monkeypatch.setattr(
        importlib.import_module("cortexshift.cli.app"), "ResumeService", resume_service
    )
    cli = CliRunner()
    for cmd in [
        ["resume", "--help"],
        ["resume", "claude", "--dry-run"],
        ["resume", "claude", "--session", first.id, "--dry-run", "--json"],
    ]:
        result = cli.invoke(app, cmd)
        assert result.exit_code == 0, result.output
    dry = json.loads(result.stdout)
    assert dry["native_session_id"] == "native-A" and dry["source_session_id"] == first.id
    assert cli.invoke(app, ["resume", "claude", "--json"]).exit_code == 1
    assert cli.invoke(app, ["resume", "claude"]).exit_code == 0
    for cmd in [["session", "list", "--json"], ["session", "show", first.id, "--json"]]:
        result = cli.invoke(app, cmd)
        assert result.exit_code == 0
        assert "\x1b" not in result.stdout
        data = json.loads(result.stdout)
        if isinstance(data, list):
            data = data[0]
            assert data["resumed_from_session_id"] == first.id
        assert data["native_session_id"] == "native-A" and data["native_resumable"] is True


@pytest.mark.parametrize(
    "scenario",
    [
        "uninitialized",
        "no_task",
        "unknown_provider",
        "missing_provider",
        "unknown_id",
        "wrong_provider",
        "wrong_task",
        "spawn_failed",
        "non_tty",
        "locked",
        "provider_failure",
    ],
)
def test_resume_cli_expected_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    cli = CliRunner()
    monkeypatch.chdir(tmp_path)
    provider = "claude"
    args = ["resume", provider]
    process = FakeProcessRunner(exit_code=42 if scenario == "provider_failure" else 0)
    service = ResumeService(
        process_runner=process,
        which_fn=lambda cmd: None if scenario == "missing_provider" else "/fake/" + cmd,
        is_tty_fn=lambda: scenario != "non_tty",
    )
    monkeypatch.setattr(
        importlib.import_module("cortexshift.cli.app"), "ResumeService", lambda: service
    )
    lock = FileWorkspaceLeaseManager().get_lease(tmp_path)
    if scenario != "uninitialized":
        project, task = seed_project(tmp_path)
        first = known(tmp_path, task.id)
        with store_at(tmp_path) as store:
            if scenario == "no_task":
                store.set_active_task_id(project.id, None)
            elif scenario in ("unknown_id", "spawn_failed"):
                store.save_session(
                    first.model_copy(
                        update={
                            "native_session_id": None if scenario == "unknown_id" else "native-A",
                            "exit_reason": SessionExitReason.SPAWN_FAILED,
                        }
                    )
                )
            elif scenario == "wrong_task":
                other = Task(project_id=project.id, title="Other", objective="Other")
                store.save_task(other)
                store.set_active_task_id(project.id, other.id)
                args.extend(["--session", first.id])
            elif scenario == "wrong_provider":
                args = ["resume", "codex", "--session", first.id]
        if scenario == "unknown_provider":
            args = ["resume", "unknown"]
        if scenario == "locked":
            assert lock.acquire()
    try:
        result = cli.invoke(app, args)
    finally:
        lock.release()
    assert result.exit_code != 0, result.output
    assert len(process.invocations) == (1 if scenario == "provider_failure" else 0)
    assert "Traceback" not in result.output
    if scenario == "provider_failure":
        assert "no fresh session was started" in result.output


@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity"])
def test_switch_cli_native_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str
) -> None:
    _, task = seed_project(tmp_path)
    first = known(tmp_path, task.id, provider)
    outgoing_provider = "codex" if provider == "claude" else "claude"
    known(tmp_path, task.id, outgoing_provider)
    monkeypatch.chdir(tmp_path)
    patch_which(monkeypatch, "cortexshift.application.switch_service", lambda cmd: "/fake/" + cmd)
    cli = CliRunner()
    result = cli.invoke(app, ["switch", provider, "--dry-run", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["target_native_mode"] == "resume_existing"
    assert data["selected_prior_target_session_id"] == first.id
    assert data["native_session_known"]
    fresh = cli.invoke(app, ["switch", provider, "--new-session", "--dry-run"])
    assert fresh.exit_code == 0 and "new_session" in fresh.stdout
    explicit = cli.invoke(
        app, ["switch", provider, "--resume-session", first.id, "--dry-run", "--json"]
    )
    assert explicit.exit_code == 0
    assert json.loads(explicit.stdout)["selected_prior_target_session_id"] == first.id
    conflict = cli.invoke(
        app, ["switch", provider, "--new-session", "--resume-session", first.id, "--dry-run"]
    )
    assert conflict.exit_code == 1 and "mutually exclusive" in conflict.output
    with store_at(tmp_path) as store:
        assert len(store.list_sessions()) == 2
        assert not store.list_handoffs(task_id=task.id)


def test_codex_plain_prompt_does_not_bootstrap(
    tmp_path: Path, codex_bootstrap: FakeCodexBootstrap
) -> None:
    seed_project(tmp_path)
    process = FakeProcessRunner()
    service = RunService(
        process_runner=process, which_fn=lambda cmd: "/fake/" + cmd, is_tty_fn=lambda: True
    )
    result = service.run("codex", prompt="USER-PROMPT", start_dir=tmp_path)
    assert result.native_session_id is None
    assert process.invocations[0]["argv"] == [
        "/fake/codex",
        *build_codex_mcp_args(),
        "USER-PROMPT",
    ]
    assert not codex_bootstrap.invocations
