"""Run with an installed artifact's Python, outside the source checkout.

Only this test driver is copied into the clean room. No source modules or test
fixtures are imported. Fake ports exercise real installed application services.
"""

import asyncio
import importlib.metadata
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import cortexshift
from cortexshift.adapters.providers.codex import CodexHandoffAdapter
from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.resume_service import ResumeService
from cortexshift.application.run_service import RunService
from cortexshift.application.switch_service import ProviderHandoffRegistry, SwitchService
from cortexshift.domain.provider import PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionStatus
from cortexshift.ports.headless_runner import HeadlessProviderRunner, HeadlessResult
from cortexshift.ports.process_runner import InteractiveProcessRunner
from cortexshift.tui.app import CortexShiftApp
from cortexshift.tui.facade import TuiFacade

ROOT = Path.cwd()
CLI = Path(sys.executable).parent / ("cortexshift.exe" if os.name == "nt" else "cortexshift")
SENTINELS = [
    "FAKE_PROMPT_SECRET_72819",
    "FAKE_PROVIDER_RESPONSE_72819",
    "FAKE_CREDENTIAL_72819",
    "FULL_GIT_PATCH_72819",
]


def is_same_directory(left: Path | str, right: Path | str) -> bool:
    """Whether two paths name the same directory on disk.

    One directory can have several spellings, and Windows has the most of them: an 8.3
    short component such as `RUNNER~1` for `runneradmin`, a different case, a junction.
    CortexShift hands a provider the *resolved* project root, while this driver knows its
    workspace by whatever spelling it was launched with -- on a GitHub Windows runner the
    temporary directory arrives short and the resolved root comes back long, so comparing
    `Path` objects compares spelling and fails on two names for one place.

    The contract is location, so ask the filesystem. `os.path.samefile` compares device
    and inode identity and is supported on Windows. The fallback only matters if a path
    stopped existing between the launch and the check, which would itself be a failure
    worth seeing rather than a crash inside the comparison.
    """
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def cli(*args: str, env: dict[str, str] | None = None, success: bool = True) -> str:
    result = subprocess.run(
        [str(CLI), *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30
    )
    if success:
        assert result.returncode == 0, (args, result.stderr)
    else:
        assert result.returncode != 0, args
        assert "Traceback" not in result.stderr
    return result.stdout


async def mcp_check(env: dict[str, str], writable: bool) -> None:
    params = StdioServerParameters(command=str(CLI), args=["mcp", "serve"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
        await client.initialize()
        tools = {tool.name for tool in (await client.list_tools()).tools}
        assert len(tools) == (10 if writable else 4)
        result = await client.call_tool("get_current_task")
        assert not result.is_error
        if writable:
            result = await client.call_tool(
                "set_current_work", {"current_work": "Installed MCP work"}
            )
            assert not result.is_error


class FakeInteractive(InteractiveProcessRunner):
    def run_interactive(self, argv, cwd, env=None):
        assert argv and is_same_directory(cwd, ROOT)
        assert env and env["CORTEXSHIFT_MCP_READ_ONLY"] == "0"
        assert SENTINELS[2] == os.environ["RELEASE_FAKE_CREDENTIAL"]
        asyncio.run(mcp_check(env, True))
        return 0


class FakeBootstrap(HeadlessProviderRunner):
    def run_headless(self, argv, cwd, timeout=30.0, env=None):
        assert is_same_directory(cwd, ROOT) and timeout > 0
        assert "--json" in argv
        assert env and env["CORTEXSHIFT_MCP_READ_ONLY"] == "1"
        return HeadlessResult(
            exit_code=0,
            stdout="\n".join(
                [
                    json.dumps(
                        {
                            "type": "thread.started",
                            "thread_id": "88888888-8888-4888-8888-888888888888",
                        }
                    ),
                    json.dumps({"type": "item.completed", "item": {"text": SENTINELS[1]}}),
                    json.dumps({"type": "turn.completed"}),
                ]
            ),
            stderr="",
        )


async def tui_check() -> None:
    app = CortexShiftApp(TuiFacade(ROOT))
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        assert app.query_one("#nav")
        for key in ["2", "3", "4", "5", "6", "7", "1"]:
            await pilot.press(key)
        await app.workers.wait_for_complete()
        assert app.is_running


def main() -> None:
    assert Path(cortexshift.__file__).is_relative_to(Path(sys.prefix))
    assert importlib.metadata.version("cortexshift") == "0.1.0"
    assert (Path(cortexshift.__file__).parent / "tui/cortexshift.tcss").is_file()
    assert "0.1.0" in cli("--version")
    assert "0.1.0" in cli("version")
    subprocess.run(
        [sys.executable, "-m", "cortexshift", "--help"], check=True, capture_output=True, timeout=30
    )
    for command in [
        "",
        "doctor",
        "init",
        "status",
        "task",
        "repo",
        "run",
        "resume",
        "session",
        "handoff",
        "switch",
        "checkpoint",
        "recover",
        "mcp",
        "tui",
    ]:
        cli(*([command] if command else []), "--help")
    cli("status", success=False)
    no_providers = dict(os.environ, PATH="")
    missing = json.loads(cli("doctor", "--json", env=no_providers))
    assert all(not p["installed"] for p in missing["providers"])
    # Deterministic discovery commands, no developer provider installation involved.
    fakebin = ROOT / "fake providers"
    fakebin.mkdir()
    for executable in ["claude", "codex", "agy"]:
        if os.name == "nt":
            path = fakebin / (executable + ".cmd")
            path.write_text("@echo off\necho 1.2.3\n", encoding="utf-8")
        else:
            path = fakebin / executable
            path.write_text('#!/bin/sh\nprintf "1.2.3\\n"\n', encoding="utf-8")
            path.chmod(0o755)
    present = json.loads(cli("doctor", "--json", env=dict(os.environ, PATH=str(fakebin))))
    assert all(p["installed"] and p["version"] == "1.2.3" for p in present["providers"])
    shutil.rmtree(fakebin)
    # No-Git core initialization remains supported.
    cli("init", env=no_providers)
    cli(
        "task",
        "start",
        "--title",
        "Release smoke",
        "--objective",
        "Validate installed CortexShift.",
    )
    cli("status", "--json")
    cli("repo", "status", "--json", env=no_providers)
    subprocess.run(["git", "init"], check=True, capture_output=True)
    # Commit only inside this disposable test repository.
    (ROOT / ".gitignore").write_text(".cortexshift/\n", encoding="utf-8")
    (ROOT / "tracked.txt").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", ".gitignore", "tracked.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Release Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "Fixture",
        ],
        check=True,
        capture_output=True,
    )
    (ROOT / "tracked.txt").write_text(SENTINELS[3], encoding="utf-8")
    cli("repo", "status", "--json")
    cli("checkpoint", "create")
    cli("checkpoint", "latest", "--json")
    cli("mcp", "status", "--json")
    asyncio.run(mcp_check(dict(os.environ, CORTEXSHIFT_PROJECT_ROOT=str(ROOT)), False))
    os.environ["RELEASE_FAKE_CREDENTIAL"] = SENTINELS[2]
    fake = FakeInteractive()
    common = {
        "process_runner": fake,
        "which_fn": lambda name: "/fake/" + name,
        "is_tty_fn": lambda: True,
    }
    first = RunService(**common).run("claude", prompt=SENTINELS[0], start_dir=ROOT)
    assert first.status == SessionStatus.COMPLETED and first.native_session_id
    result = SwitchService(
        registry=ProviderHandoffRegistry([CodexHandoffAdapter(headless_runner=FakeBootstrap())]),
        **common,
    ).switch("codex", start_dir=ROOT)
    assert result.target_session.native_session_id
    resumed = ResumeService(**common).resume("codex", start_dir=ROOT)
    assert resumed.native_session_id == result.target_session.native_session_id
    assert resumed.resumed_from_session_id == result.target_session.id
    database = ROOT / ".cortexshift/state.sqlite3"
    with SQLiteStateStore(database) as store:
        assert store.get_schema_version() == 6
        task = store.get_task(first.task_id)
        assert task and task.current_work == "Installed MCP work"
        checkpoint = store.get_latest_checkpoint(task.id)
        assert checkpoint and checkpoint.kind.value == "session_end"
        stale = Session(task_id=task.id, provider_id=PROVIDER_CODEX, status=SessionStatus.RUNNING)
        store.save_session(stale)
    cli("recover", "--dry-run")
    cli("recover")
    with SQLiteStateStore(database) as store:
        recovered = store.get_session(stale.id)
        assert recovered and recovered.status == SessionStatus.INTERRUPTED
        assert recovered.ended_at is None and recovered.reconciled_at
    asyncio.run(tui_check())
    with sqlite3.connect(database) as connection:
        persisted = "\n".join(connection.iterdump())
    for sentinel in SENTINELS:
        assert sentinel not in persisted, "Private transport data leaked to SQLite"
    assert {p.name for p in database.parent.iterdir()} <= {
        "state.sqlite3",
        "state.sqlite3-wal",
        "state.sqlite3-shm",
        "agent.lock",
    }
    print("Installed CLI/MCP/TUI, fake handoff/resume/recovery, persistence and privacy: PASS")


if __name__ == "__main__":
    main()
