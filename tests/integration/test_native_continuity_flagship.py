"""One task, six invocations, three native identities; no provider private file access."""

import builtins
import json
import subprocess
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from cortexshift.adapters.providers.antigravity import AntigravityHandoffAdapter
from cortexshift.adapters.providers.claude import ClaudeHandoffAdapter
from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.resume_service import ResumeService
from cortexshift.application.run_service import RunService
from cortexshift.application.switch_service import ProviderHandoffRegistry, SwitchService
from cortexshift.domain.errors import HandoffDeliveryError, NativeResumeError
from cortexshift.domain.session import Session
from cortexshift.ports.headless_runner import HeadlessResult
from tests.factories import seed_project
from tests.unit.test_switch_service import FakeProcessRunner


class NativeBootstrap:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.counts = {"codex": 0, "agy": 0}
        self.fail = False

    def run_headless(
        self,
        argv: list[str],
        cwd: Path | str,
        timeout: float = 300.0,
        env: dict[str, str] | None = None,
    ) -> HeadlessResult:
        self.calls.append(argv)
        if self.fail:
            return HeadlessResult(exit_code=1, stdout="PRIVATE-FAILURE", stderr="PRIVATE-FAILURE")
        provider = Path(argv[0]).name
        flag = "--json" if provider == "codex" else "--conversation"
        existing = ("resume" in argv) if provider == "codex" else flag in argv
        if existing:
            native_id = argv[argv.index(flag) + 1]
        else:
            self.counts[provider] += 1
            native_id = f"{provider}-native-{self.counts[provider]}"
        if provider == "codex":
            output = "\n".join(
                [
                    json.dumps({"type": "thread.started", "thread_id": native_id}),
                    json.dumps({"type": "item.completed", "response": "PRIVATE-RESPONSE"}),
                    '{"type":"turn.completed"}',
                ]
            )
        else:
            output = json.dumps(
                {"conversation_id": native_id, "status": "SUCCESS", "response": "PRIVATE-RESPONSE"}
            )
        return HeadlessResult(exit_code=0, stdout=output, stderr="")


@pytest.fixture
def native_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    project, task = seed_project(tmp_path)
    process = FakeProcessRunner()
    bootstrap = NativeBootstrap()
    registry = ProviderHandoffRegistry(
        [
            ClaudeHandoffAdapter(),
            CodexHandoffAdapter(headless_runner=bootstrap),
            AntigravityHandoffAdapter(headless_runner=bootstrap),
        ]
    )
    switch = SwitchService(
        registry=registry,
        process_runner=process,
        which_fn=lambda cmd: "/fake/" + cmd,
        is_tty_fn=lambda: True,
    )
    run = RunService(
        process_runner=process, which_fn=lambda cmd: "/fake/" + cmd, is_tty_fn=lambda: True
    )
    # Boundary guard fails on attempts to inspect provider transcript/cache/auth storage.
    original_open = builtins.open
    original_path_open = Path.open
    forbidden_roots = [
        Path.home() / name for name in (".claude", ".codex", ".gemini", ".antigravity")
    ]

    def check(file: object) -> None:
        if isinstance(file, (str, Path)):
            path = Path(file).absolute()
            assert not any(path == root or root in path.parents for root in forbidden_roots), path

    def guarded_open(file, *args, **kwargs):
        check(file)
        return original_open(file, *args, **kwargs)

    def guarded_path_open(self, *args, **kwargs):
        check(self)
        return original_path_open(self, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    return project, task, run, switch, process, bootstrap


def test_flagship_a_b_c_a_b_c_and_restart(tmp_path: Path, native_workflow) -> None:
    project, task, run, switch, process, bootstrap = native_workflow
    first = run.run("claude", start_dir=tmp_path)
    assert UUID(first.native_session_id).version == 4
    sessions = [first]
    handoffs = []
    for n, provider in enumerate(["codex", "antigravity", "claude", "codex", "antigravity"]):
        (tmp_path / f"change_{n}.py").write_text(f"# edit by previous provider {n}\n")
        result = switch.switch(provider, start_dir=tmp_path)
        sessions.append(result.target_session)
        handoffs.append(result.handoff)
        assert result.handoff.source_session_id == sessions[-2].id
        assert f"change_{n}.py" in result.handoff.payload.files_touched
        assert result.handoff.target_session_id == sessions[-1].id
    assert len({s.id for s in sessions}) == 6
    assert {s.task_id for s in sessions} == {task.id}
    for index in range(3):
        original, resumed = sessions[index], sessions[index + 3]
        assert original.native_session_id == resumed.native_session_id
        assert original.resumed_from_session_id is None
        assert resumed.resumed_from_session_id == original.id
    assert bootstrap.counts == {"codex": 1, "agy": 1}
    assert len(bootstrap.calls) == 4
    assert len({h.git_snapshot_id for h in handoffs}) == 5
    assert len({h.id for h in handoffs}) == 5
    claude_return = process.invocations[3]["argv"]
    assert claude_return[1:3] == ["--resume", first.native_session_id]
    prompts = [claude_return[-1]]
    for call in bootstrap.calls:
        prompt = call[-1] if "exec" in call else call[call.index("-p") + 1]
        prompts.append(prompt)
        if "exec" in call:
            assert call[1:4] == ["exec", "--sandbox", "read-only"]
        else:
            assert "--mode=plan" in call
    for prompt in prompts:
        assert "handoff supersedes stale assumptions" in prompt
        assert "Re-inspect current repository" in prompt
        assert task.id in prompt
    for call in [*bootstrap.calls, *(i["argv"] for i in process.invocations)]:
        assert "--last" not in call and "--continue" not in call
    # Reopen with entirely fresh store objects: all identities, lineage and observations survive.
    for _ in range(2):
        with SQLiteStateStore(tmp_path / ".cortexshift/state.sqlite3") as store:
            assert store.get_task(task.id) == task
            assert store.get_active_task_id(project.id) == task.id
            assert len(store.list_tasks(project.id)) == 1
            assert list(reversed(store.list_sessions(limit=None))) == sessions
            assert len(store.list_handoffs(project_id=project.id)) == 5
            assert len(store.list_snapshots(project.id)) >= 5
            assert "PRIVATE-RESPONSE" not in str(store.list_sessions()) + str(
                store.list_handoffs(project_id=project.id)
            )
    # Physical private-state files contain no bootstrap responses or rendered bootstrap prompts.
    for path in (tmp_path / ".cortexshift").iterdir():
        if path.is_file():
            data = path.read_bytes()
            assert b"PRIVATE-RESPONSE" not in data
            assert b"This is a CortexShift handoff bootstrap" not in data


@pytest.mark.parametrize("provider", ["claude", "codex", "antigravity"])
def test_force_new_explicit_target_and_zero_effect_dry_runs(
    tmp_path: Path, native_workflow, provider: str
) -> None:
    project, task, run, switch, process, bootstrap = native_workflow
    first = run.run("claude", start_dir=tmp_path)
    by_provider = {"claude": first}
    for target in ["codex", "antigravity"]:
        by_provider[target] = switch.switch(target, start_dir=tmp_path).target_session
    prior = by_provider[provider]
    outgoing = next(s for p, s in by_provider.items() if p != provider)

    def snapshot() -> tuple[Any, ...]:
        with SQLiteStateStore(tmp_path / ".cortexshift/state.sqlite3") as store:
            return (
                store.list_sessions(),
                store.list_handoffs(project_id=project.id),
                store.list_snapshots(project.id),
                store.get_task(task.id),
            )

    before = snapshot()
    calls = len(process.invocations), len(bootstrap.calls)
    preview = switch.dry_run(provider, from_session_id=outgoing.id, start_dir=tmp_path)
    assert preview.target_native_mode == "resume_existing"
    assert preview.selected_prior_target_session_id == prior.id
    assert preview.native_session_known
    fresh = switch.dry_run(
        provider, from_session_id=outgoing.id, start_dir=tmp_path, new_session=True
    )
    assert fresh.target_native_mode == "new_session" and not fresh.native_session_known
    assert fresh.selected_prior_target_session_id is None
    with pytest.raises(NativeResumeError, match="mutually exclusive"):
        switch.switch(
            provider,
            from_session_id=outgoing.id,
            start_dir=tmp_path,
            new_session=True,
            resume_session_id=prior.id,
        )
    assert snapshot() == before
    assert calls == (len(process.invocations), len(bootstrap.calls))
    result = switch.switch(
        provider, from_session_id=outgoing.id, start_dir=tmp_path, new_session=True
    )
    assert result.target_session.native_session_id != prior.native_session_id
    assert result.target_session.resumed_from_session_id is None
    # Explicit target override can return to the original conversation rather than the newest one.
    result = switch.switch(
        provider, from_session_id=outgoing.id, start_dir=tmp_path, resume_session_id=prior.id
    )
    assert result.target_session.native_session_id == prior.native_session_id
    assert result.target_session.resumed_from_session_id == prior.id


@pytest.mark.parametrize("provider", ["codex", "antigravity"])
def test_return_bootstrap_failure_preserves_original_and_does_not_fallback(
    tmp_path: Path, native_workflow, provider: str
) -> None:
    project, task, run, switch, process, bootstrap = native_workflow
    run.run("claude", start_dir=tmp_path)
    old = switch.switch(provider, start_dir=tmp_path).target_session
    switch.switch("claude", start_dir=tmp_path)
    before = len(process.invocations)
    bootstrap.fail = True
    with pytest.raises(HandoffDeliveryError):
        switch.switch(provider, start_dir=tmp_path)
    assert len(process.invocations) == before
    with SQLiteStateStore(tmp_path / ".cortexshift/state.sqlite3") as store:
        assert store.get_session(old.id) == old
        assert store.list_handoffs(project_id=project.id)[0].status == "failed"
    # Plain exact resume does not bootstrap, even after a failed handoff attempt.
    resumed = ResumeService(
        process_runner=process, which_fn=lambda cmd: "/fake/" + cmd, is_tty_fn=lambda: True
    ).resume(provider, start_dir=tmp_path)
    assert isinstance(resumed, Session) and resumed.resumed_from_session_id == old.id
