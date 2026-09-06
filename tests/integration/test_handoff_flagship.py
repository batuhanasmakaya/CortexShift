"""Flagship integration test for CortexShift's central product promise.

One Task. Multiple coding agents. No need to manually re-explain the work.

Exercises the complete real product flow against a disposable Git repository and fake
provider executables:

    Task created -> fake Claude works -> repository changes -> Claude session ends
    -> switch codex   -> Codex receives the canonical handoff
    -> more changes
    -> switch antigravity -> read-only plan bootstrap ingests the handoff,
                             the captured conversation is resumed interactively

No network. No provider subscription. No real model call.
"""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from cortexshift.adapters.providers.codex import CODEX_BOOTSTRAP_PREFIX
from cortexshift.adapters.sqlite.migrations import CURRENT_SCHEMA_VERSION
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.switch_service import SwitchService
from cortexshift.cli.app import app
from cortexshift.domain.handoff import HandoffStatus
from cortexshift.domain.provider import PROVIDER_ANTIGRAVITY, PROVIDER_CLAUDE, PROVIDER_CODEX
from cortexshift.domain.session import SessionStatus
from cortexshift.ports.headless_runner import HeadlessProviderRunner, HeadlessResult
from cortexshift.ports.process_runner import InteractiveProcessRunner
from tests.factories import FakeCodexBootstrap, patch_which, seed_session

runner = CliRunner()

BOOTSTRAP_CONVERSATION_ID = "test-conversation-123"
BOOTSTRAP_SECRET_RESPONSE = "Plan: verify auth module, then wire refresh. NEVER-PERSIST-ME."


class RecordingProcessRunner(InteractiveProcessRunner):
    """Fake interactive provider capturing the exact argument vector it receives."""

    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []

    def run_interactive(
        self,
        argv: list[str],
        cwd: Path | str,
        env: dict[str, str] | None = None,
    ) -> int:
        self.invocations.append({"argv": list(argv), "cwd": str(cwd)})
        return 0


class RecordingHeadlessRunner(HeadlessProviderRunner):
    """Fake Antigravity headless bootstrap returning a documented JSON envelope."""

    def __init__(self) -> None:
        self.invocations: list[dict[str, Any]] = []

    def run_headless(
        self,
        argv: list[str],
        cwd: Path | str,
        timeout: float = 300.0,
        env: dict[str, str] | None = None,
    ) -> HeadlessResult:
        self.invocations.append({"argv": list(argv), "cwd": str(cwd)})
        return HeadlessResult(
            exit_code=0,
            stdout=json.dumps(
                {
                    "conversation_id": BOOTSTRAP_CONVERSATION_ID,
                    "status": "SUCCESS",
                    "response": BOOTSTRAP_SECRET_RESPONSE,
                    "usage": {"input_tokens": 4321},
                }
            ),
            stderr="",
        )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A disposable real Git repository with an initialized CortexShift project."""
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "Test User")
    _git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "README.md").write_text("# Flagship Repo\n")
    _git(tmp_path, "add", "README.md")
    _git(tmp_path, "commit", "-m", "initial commit")

    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--name", "FlagshipProject"]).exit_code == 0
    return tmp_path


@pytest.fixture
def fake_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[RecordingProcessRunner, RecordingHeadlessRunner]:
    """Install fake provider executables, interactive runner, and headless runner."""
    process_runner = RecordingProcessRunner()
    headless_runner = RecordingHeadlessRunner()

    patch_which(
        monkeypatch, "cortexshift.application.switch_service", lambda cmd: f"/fake/bin/{cmd}"
    )
    patch_which(monkeypatch, "cortexshift.application.run_service", lambda cmd: f"/fake/bin/{cmd}")
    monkeypatch.setattr(
        "cortexshift.application.switch_service.SubprocessInteractiveProcessRunner",
        lambda: process_runner,
    )
    monkeypatch.setattr(
        "cortexshift.application.run_service.SubprocessInteractiveProcessRunner",
        lambda: process_runner,
    )
    monkeypatch.setattr(
        "cortexshift.adapters.providers.antigravity.SubprocessHeadlessProviderRunner",
        lambda: headless_runner,
    )
    monkeypatch.setattr(SwitchService, "_check_tty", staticmethod(lambda: True))
    monkeypatch.setattr(
        "cortexshift.application.run_service.RunService._check_tty", staticmethod(lambda: True)
    )
    return process_runner, headless_runner


def _db(repo: Path) -> SQLiteStateStore:
    return SQLiteStateStore(repo / ".cortexshift" / "state.sqlite3", auto_migrate=False)


def test_flagship_claude_to_codex_to_antigravity(
    repo: Path,
    fake_providers: tuple[RecordingProcessRunner, RecordingHeadlessRunner],
    codex_bootstrap: FakeCodexBootstrap,
) -> None:
    """One task moves Claude -> Codex -> Antigravity carrying full canonical context."""
    process_runner, headless_runner = fake_providers

    # 1-2. Create the active task with objective, requirements, constraints, and backlog.
    assert (
        runner.invoke(
            app,
            [
                "task",
                "start",
                "--title",
                "OAuth2 PKCE support",
                "--objective",
                "Add OAuth2 PKCE support to the auth layer.",
                "--requirement",
                "Support refresh token rotation",
                "--requirement",
                "Keep the public auth API stable",
                "--constraint",
                "No new third-party dependencies",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app,
            [
                "task",
                "update",
                "--add-remaining",
                "Wire token refresh",
                "--add-remaining",
                "Add integration tests",
            ],
        ).exit_code
        == 0
    )

    with _db(repo) as store:
        project = store.get_default_project()
        assert project is not None
        task_id = store.get_active_task_id(project.id)
    assert task_id is not None

    # 3. Fake Claude works on the task and modifies the repository.
    (repo / "auth.py").write_text("def pkce_verifier():\n    return 'stub'\n")
    assert runner.invoke(app, ["run", "claude"]).exit_code == 0
    (repo / "README.md").write_text("# Flagship Repo\n\nAuth work in progress.\n")

    # 4. Record progress: one item completed, in-flight work set, backlog preserved.
    assert (
        runner.invoke(
            app,
            [
                "task",
                "update",
                "--add-completed",
                "Scaffolded the PKCE verifier helper",
                "--work",
                "Implementing the token exchange call",
            ],
        ).exit_code
        == 0
    )

    # 5-6. Claude's session has ended; hand the same task to Codex.
    codex_result = runner.invoke(app, ["switch", "codex"])
    assert codex_result.exit_code == 0, codex_result.stdout

    assert len(process_runner.invocations) == 2
    codex_call = process_runner.invocations[1]
    assert codex_call["argv"][0] == "/fake/bin/codex"
    assert "-c" in codex_call["argv"]
    assert codex_call["argv"][-2:] == ["resume", "test-codex-native-id"]
    assert codex_call["cwd"] == str(repo)

    context = codex_bootstrap.invocations[-1][-1]
    assert "CORTEXSHIFT HANDOFF PROTOCOL v1" in context
    assert task_id in context
    assert "Add OAuth2 PKCE support to the auth layer." in context
    assert "Support refresh token rotation" in context
    assert "Keep the public auth API stable" in context
    assert "No new third-party dependencies" in context
    assert "Scaffolded the PKCE verifier helper" in context
    assert "Implementing the token exchange call" in context
    assert "Wire token refresh" in context
    assert "Add integration tests" in context
    assert "auth.py" in context
    assert "README.md" in context
    assert "claude" in context
    assert "AUTHORITY ORDER" in context
    assert "Do NOT restart the task from scratch." in context
    assert "Run the project's relevant tests" in context

    # 8-9. Codex exits; the repository changes again.
    (repo / "tokens.py").write_text("def exchange():\n    return None\n")
    _git(repo, "add", "auth.py")

    # 10-13. Hand the same task to Antigravity: plan bootstrap, then conversation resume.
    agy_result = runner.invoke(app, ["switch", "antigravity"])
    assert agy_result.exit_code == 0, agy_result.stdout

    assert len(headless_runner.invocations) == 1
    bootstrap_argv = headless_runner.invocations[0]["argv"]
    assert bootstrap_argv[0] == "/fake/bin/agy"
    assert "--mode=plan" in bootstrap_argv
    assert bootstrap_argv[bootstrap_argv.index("--output-format") + 1] == "json"
    bootstrap_prompt = bootstrap_argv[bootstrap_argv.index("-p") + 1]
    assert "Remain read-only." in bootstrap_prompt
    assert "CORTEXSHIFT HANDOFF PROTOCOL v1" in bootstrap_prompt
    assert "tokens.py" in bootstrap_prompt
    assert "--dangerously-skip-permissions" not in bootstrap_argv

    assert len(process_runner.invocations) == 3
    assert process_runner.invocations[2]["argv"] == [
        "/fake/bin/agy",
        "--conversation",
        BOOTSTRAP_CONVERSATION_ID,
    ]

    # 14. Verify the resulting canonical state.
    with _db(repo) as store:
        assert store.get_active_task_id(project.id) == task_id

        handoffs = store.list_handoffs(project_id=project.id)
        assert len(handoffs) == 2
        assert all(h.status == HandoffStatus.DELIVERED for h in handoffs)
        assert all(h.task_id == task_id for h in handoffs)
        assert all(h.protocol_version == 1 for h in handoffs)

        agy_handoff, codex_handoff = handoffs
        assert codex_handoff.source_provider_id == PROVIDER_CLAUDE
        assert codex_handoff.target_provider_id == PROVIDER_CODEX
        assert agy_handoff.source_provider_id == PROVIDER_CODEX
        assert agy_handoff.target_provider_id == PROVIDER_ANTIGRAVITY

        sessions = store.list_sessions(project_id=project.id)
        assert len(sessions) == 3
        assert [s.provider_id for s in sessions] == [
            PROVIDER_ANTIGRAVITY,
            PROVIDER_CODEX,
            PROVIDER_CLAUDE,
        ]
        assert all(s.task_id == task_id for s in sessions)

        # Each handoff points at the target session it launched.
        assert codex_handoff.target_session_id == sessions[1].id
        assert agy_handoff.target_session_id == sessions[0].id
        # The Antigravity handoff sources from the Codex session it followed.
        assert agy_handoff.source_session_id == sessions[1].id
        assert codex_handoff.source_session_id == sessions[2].id

        # Antigravity's native conversation identifier is bound to its CortexShift session.
        assert sessions[0].native_session_id == BOOTSTRAP_CONVERSATION_ID
        assert sessions[1].native_session_id == "test-codex-native-id"
        assert sessions[2].native_session_id is not None

        # Each switch captured its own Git snapshot.
        snapshots = store.list_snapshots(project_id=project.id, limit=50)
        handoff_snapshot_ids = {h.git_snapshot_id for h in handoffs}
        assert len(handoff_snapshot_ids) == 2
        assert None not in handoff_snapshot_ids
        assert handoff_snapshot_ids <= {s.id for s in snapshots}

        # Task progress was never inferred from provider process outcomes.
        task = store.get_task(task_id)
        assert task is not None
        assert task.completed_items == ["Scaffolded the PKCE verifier helper"]
        assert task.remaining_items == ["Wire token refresh", "Add integration tests"]
        assert task.current_work == "Implementing the token exchange call"
        assert task.status != "completed"

        # The Antigravity bootstrap response was parsed and discarded.
        serialized = json.dumps([h.model_dump(mode="json") for h in handoffs])
        assert BOOTSTRAP_SECRET_RESPONSE not in serialized
        assert "input_tokens" not in serialized
        assert context not in serialized
        assert "AUTHORITY ORDER" not in serialized


def test_flagship_state_survives_full_restart(
    repo: Path,
    fake_providers: tuple[RecordingProcessRunner, RecordingHeadlessRunner],
    codex_bootstrap: FakeCodexBootstrap,
) -> None:
    """All task, session, snapshot, handoff, and native-ID links survive a fresh process."""
    test_flagship_claude_to_codex_to_antigravity(repo, fake_providers, codex_bootstrap)

    with _db(repo) as store:
        project = store.get_default_project()
        assert project is not None
        expected_task = store.get_active_task_id(project.id)
        expected_handoffs = [h.id for h in store.list_handoffs(project_id=project.id)]
        expected_sessions = [s.id for s in store.list_sessions(project_id=project.id)]

    # Simulate a completely fresh CortexShift process against the same on-disk state.
    with _db(repo) as store:
        reopened_project = store.get_default_project()
        assert reopened_project is not None
        assert reopened_project.id == project.id
        assert store.get_schema_version() == CURRENT_SCHEMA_VERSION
        assert store.get_active_task_id(reopened_project.id) == expected_task

        handoffs = store.list_handoffs(project_id=reopened_project.id)
        assert [h.id for h in handoffs] == expected_handoffs
        sessions = store.list_sessions(project_id=reopened_project.id)
        assert [s.id for s in sessions] == expected_sessions

        for handoff in handoffs:
            assert handoff.status == HandoffStatus.DELIVERED
            assert handoff.delivered_at is not None
            assert handoff.payload.protocol_version == 1
            assert handoff.payload.original_objective.startswith("Add OAuth2 PKCE")
            assert handoff.payload.requirements == [
                "Support refresh token rotation",
                "Keep the public auth API stable",
            ]
            assert handoff.payload.constraints == ["No new third-party dependencies"]
            assert handoff.payload.git_state.available is True
            assert handoff.git_snapshot_id is not None
            assert store.get_snapshot(handoff.git_snapshot_id) is not None
            assert handoff.target_session_id is not None
            assert store.get_session(handoff.target_session_id) is not None
            assert store.get_session(handoff.source_session_id) is not None

        antigravity_session = next(s for s in sessions if s.provider_id == PROVIDER_ANTIGRAVITY)
        assert antigravity_session.native_session_id == BOOTSTRAP_CONVERSATION_ID
        assert antigravity_session.status == SessionStatus.COMPLETED

    # The CLI reads the same durable state back in a fresh invocation.
    listed = json.loads(runner.invoke(app, ["handoff", "list", "--json"]).stdout)
    assert [h["id"] for h in listed] == expected_handoffs

    shown = json.loads(
        runner.invoke(app, ["handoff", "show", expected_handoffs[0], "--json"]).stdout
    )
    assert shown["protocol_version"] == 1
    assert shown["payload"]["task_id"] == expected_task
    assert shown["target_provider_id"] == "antigravity"


def test_historical_handoff_is_not_current_repository_truth(
    repo: Path,
    fake_providers: tuple[RecordingProcessRunner, RecordingHeadlessRunner],
) -> None:
    """A stored handoff stays a fixed historical observation while live Git moves on."""
    assert (
        runner.invoke(
            app,
            [
                "task",
                "start",
                "--title",
                "Live vs historical",
                "--objective",
                "Prove handoffs are historical observations.",
            ],
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["run", "claude"]).exit_code == 0

    (repo / "first.py").write_text("first\n")
    assert runner.invoke(app, ["switch", "codex"]).exit_code == 0

    with _db(repo) as store:
        handoff = store.list_handoffs()[0]
    original = handoff.model_dump(mode="json")
    assert "first.py" in handoff.payload.files_touched
    assert "second.py" not in handoff.payload.files_touched

    # The repository changes after the handoff was captured.
    (repo / "second.py").write_text("second\n")

    with _db(repo) as store:
        reloaded = store.get_handoff(handoff.id)
    assert reloaded is not None
    assert reloaded.model_dump(mode="json") == original
    assert "second.py" not in reloaded.payload.files_touched

    # Live inspection reflects current reality, unlike the stored handoff.
    live = json.loads(runner.invoke(app, ["repo", "status", "--json"]).stdout)
    live_paths = set(live["snapshot"]["untracked_files"])
    assert "second.py" in live_paths
    assert "first.py" in live_paths


def test_outgoing_provider_missing_does_not_block_handoff(
    repo: Path, monkeypatch: pytest.MonkeyPatch, codex_bootstrap: FakeCodexBootstrap
) -> None:
    """Regression: Claude being gone must not stop the task from moving to Codex.

    This is the exact real-world scenario CortexShift exists for: Claude's quota is
    exhausted and its CLI can no longer be invoked, yet the work must continue.
    """
    process_runner = RecordingProcessRunner()
    resolved: list[str] = []

    def only_codex_installed(command: str) -> str | None:
        resolved.append(command)
        return "/fake/bin/codex" if command == "codex" else None

    monkeypatch.setattr(
        "cortexshift.application.switch_service.SubprocessInteractiveProcessRunner",
        lambda: process_runner,
    )
    monkeypatch.setattr(SwitchService, "_check_tty", staticmethod(lambda: True))

    assert (
        runner.invoke(
            app,
            [
                "task",
                "start",
                "--title",
                "Continue after quota",
                "--objective",
                "Finish the auth refactor after Claude's quota ran out.",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["task", "update", "--add-remaining", "Finish the token exchange"]
        ).exit_code
        == 0
    )

    # A historical Claude session exists, recorded while Claude was still usable.
    with _db(repo) as store:
        project = store.get_default_project()
        assert project is not None
        task_id = store.get_active_task_id(project.id)
        assert task_id is not None
    claude_session = seed_session(repo, task_id, provider_id=PROVIDER_CLAUDE)

    (repo / "auth.py").write_text("partial work\n")

    # Claude is no longer resolvable on PATH.
    patch_which(monkeypatch, "cortexshift.application.switch_service", only_codex_installed)

    result = runner.invoke(app, ["switch", "codex"])
    assert result.exit_code == 0, result.stdout

    # CortexShift never even looked for the outgoing provider's executable.
    assert "claude" not in resolved
    assert "codex" in resolved

    context = codex_bootstrap.invocations[-1][-1]
    assert "Finish the auth refactor after Claude's quota ran out." in context
    assert "Finish the token exchange" in context
    assert "auth.py" in context

    with _db(repo) as store:
        handoff = store.list_handoffs()[0]
        assert handoff.status == HandoffStatus.DELIVERED
        assert handoff.source_provider_id == PROVIDER_CLAUDE
        assert handoff.source_session_id == claude_session.id
        assert handoff.target_provider_id == PROVIDER_CODEX


def test_context_budget_bounds_transport_but_not_canonical_payload(
    repo: Path,
    fake_providers: tuple[RecordingProcessRunner, RecordingHeadlessRunner],
    codex_bootstrap: FakeCodexBootstrap,
) -> None:
    """A huge task is delivered bounded, while the persisted payload stays complete."""
    process_runner, _ = fake_providers

    # Canonical task items are stored stripped, so build the expected list the same way.
    completed = [
        (f"Completed milestone {i} " + "with a long description " * 6).strip() for i in range(400)
    ]
    assert (
        runner.invoke(
            app,
            [
                "task",
                "start",
                "--title",
                "Enormous task",
                "--objective",
                "Prove the context budget bounds transport only.",
            ],
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(app, ["task", "update", "--add-remaining", "Finish the last step"]).exit_code
        == 0
    )
    for chunk_start in range(0, len(completed), 50):
        args = ["task", "update"]
        for item in completed[chunk_start : chunk_start + 50]:
            args += ["--add-completed", item]
        assert runner.invoke(app, args).exit_code == 0

    for i in range(120):
        (repo / f"generated_{i}.py").write_text(f"# file {i}\n")

    assert runner.invoke(app, ["run", "claude"]).exit_code == 0
    assert runner.invoke(app, ["switch", "codex"]).exit_code == 0

    context = codex_bootstrap.invocations[-1][-1]
    assert len(context.removeprefix(CODEX_BOOTSTRAP_PREFIX)) <= 48_000
    assert "omitted from injected context." in context
    assert "cortexshift handoff show handoff_" in context
    # Mandatory context survives.
    assert "Prove the context budget bounds transport only." in context
    assert "Finish the last step" in context
    assert "AUTHORITY ORDER" in context
    assert "--- START HERE ---" in context

    with _db(repo) as store:
        handoff = store.list_handoffs()[0]

    # The canonical persisted payload is never truncated.
    assert handoff.payload.completed == completed
    assert len(handoff.payload.files_touched) >= 120

    shown = json.loads(runner.invoke(app, ["handoff", "show", handoff.id, "--json"]).stdout)
    assert len(shown["payload"]["completed"]) == 400


def test_switch_argv_stays_inert_under_hostile_task_content(
    repo: Path,
    fake_providers: tuple[RecordingProcessRunner, RecordingHeadlessRunner],
    codex_bootstrap: FakeCodexBootstrap,
) -> None:
    """Hostile canonical content and filenames never escape the argv boundary."""
    process_runner, headless_runner = fake_providers
    marker = repo / "hacked"

    assert (
        runner.invoke(
            app,
            [
                "task",
                "start",
                "--title",
                "Injection probe",
                "--objective",
                f'Fix $(touch {marker}) and `rm -rf /` with "quotes"',
            ],
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["task", "update", "--add-remaining", "; rm -rf / #"]).exit_code == 0
    (repo / "$(touch hacked).py").write_text("payload\n")

    assert runner.invoke(app, ["run", "claude"]).exit_code == 0
    assert runner.invoke(app, ["switch", "codex"]).exit_code == 0
    assert runner.invoke(app, ["switch", "antigravity"]).exit_code == 0

    codex_argv = process_runner.invocations[1]["argv"]
    assert codex_argv[0] == "/fake/bin/codex"
    assert "-c" in codex_argv
    assert codex_argv[-2:] == ["resume", "test-codex-native-id"]
    assert "$(touch" in codex_bootstrap.invocations[-1][-1]

    bootstrap_argv = headless_runner.invocations[0]["argv"]
    assert sum(1 for arg in bootstrap_argv if "$(touch" in arg) == 1

    assert not marker.exists()
    assert not (repo / "hacked").exists()


def test_stale_running_session_does_not_block_switch(
    repo: Path,
    fake_providers: tuple[RecordingProcessRunner, RecordingHeadlessRunner],
) -> None:
    """A stale `running` database record never blocks a switch when the OS lease is free."""
    assert (
        runner.invoke(
            app,
            [
                "task",
                "start",
                "--title",
                "Stale session",
                "--objective",
                "Prove stale DB rows never block the lease.",
            ],
        ).exit_code
        == 0
    )

    from cortexshift.domain.session import Session

    with _db(repo) as store:
        project = store.get_default_project()
        assert project is not None
        task_id = store.get_active_task_id(project.id)
        assert task_id is not None
        store.save_session(
            Session(
                task_id=task_id,
                provider_id=PROVIDER_CLAUDE,
                status=SessionStatus.RUNNING,
            )
        )

    result = runner.invoke(app, ["switch", "codex"])
    assert result.exit_code == 0, result.stdout

    with _db(repo) as store:
        assert store.list_handoffs()[0].status == HandoffStatus.DELIVERED


def test_active_workspace_lease_blocks_switch(
    repo: Path,
    fake_providers: tuple[RecordingProcessRunner, RecordingHeadlessRunner],
) -> None:
    """A genuinely held OS lease blocks a concurrent switch from another terminal."""
    assert (
        runner.invoke(
            app,
            [
                "task",
                "start",
                "--title",
                "Concurrency",
                "--objective",
                "Prove the single-mutating-agent invariant holds for switch.",
            ],
        ).exit_code
        == 0
    )
    assert runner.invoke(app, ["run", "claude"]).exit_code == 0

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time;"
            "sys.path.insert(0, r'" + str(Path(__file__).resolve().parents[2] / "src") + "');"
            "from pathlib import Path;"
            "from cortexshift.adapters.workspace_lease import FileWorkspaceLeaseManager;"
            "lease = FileWorkspaceLeaseManager().get_lease(Path(r'" + str(repo) + "'));"
            "assert lease.acquire();"
            "print('LOCKED', flush=True);"
            "time.sleep(30)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "LOCKED"

        result = runner.invoke(app, ["switch", "codex"])
        assert result.exit_code == 1
        output = result.stderr + result.stdout
        assert "Another CortexShift agent session is already active" in output
        assert "delete" not in output.lower()
    finally:
        holder.kill()
        holder.wait()

    with _db(repo) as store:
        assert store.list_handoffs() == []
