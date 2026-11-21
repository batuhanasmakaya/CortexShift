import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.adapters.workspace_lease import LOCK_FILE_NAME, FileWorkspaceLease
from cortexshift.application.checkpoint_builder import CheckpointBuilder
from cortexshift.application.init_service import ProjectInitializationService
from cortexshift.application.locator import ProjectLocator
from cortexshift.application.task_workspace import TaskWorkspaceService
from cortexshift.desktop.bridge import MAX_REQUEST_BYTES, Bridge
from cortexshift.domain.checkpoint import CheckpointKind
from cortexshift.domain.provider import PROVIDER_CODEX
from cortexshift.domain.session import Session, SessionExitReason, SessionStatus
from tests.factories import make_inspection

OBJECTIVE = "# Real objective\n\n" + "🧪" * 20_000


def request(bridge: Bridge, op: str, **args: Any) -> dict[str, Any]:
    return bridge.respond(json.dumps({"version": 1, "id": 1, "op": op, **args}).encode())


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, dict[str, str], str, str, str]:
    project = ProjectInitializationService().initialize(tmp_path, "Real workspace").project
    task = TaskWorkspaceService().start_task("Persisted task", OBJECTIVE, start_dir=tmp_path)
    session = Session(
        task_id=task.id,
        provider_id=PROVIDER_CODEX,
        native_session_id="known-session",
        status=SessionStatus.COMPLETED,
        exit_code=0,
        exit_reason=SessionExitReason.NORMAL_COMPLETION,
    )
    checkpoint = CheckpointBuilder.build(
        project,
        task,
        make_inspection(tmp_path, project.id),
        CheckpointKind.MANUAL,
        session=session,
        decisions=["Use SQLite"],
    )
    with SQLiteStateStore(ProjectLocator.get_database_path(tmp_path)) as store:
        store.save_session(session)
        store.save_checkpoint(checkpoint)
    return (
        tmp_path,
        {"root": str(tmp_path.resolve()), "project_id": project.id},
        (task.id),
        session.id,
        checkpoint.id,
    )


def test_handshake_correlation_and_validation() -> None:
    bridge = Bridge()
    assert request(bridge, "hello", id=12)["id"] == 12
    assert request(bridge, "hello")["result"]["protocol"] == 1
    assert request(bridge, "execute")["error"]["code"] == "unknown_operation"
    assert request(bridge, "hello", extra=True)["error"]["code"] == "invalid_request"
    assert bridge.respond(b"{")["error"]["code"] == "invalid_request"
    assert bridge.respond(b" " * (MAX_REQUEST_BYTES + 1))["error"]["code"] == "frame_too_large"


def test_real_state_and_no_mutation(workspace: tuple[Path, dict[str, str], str, str, str]) -> None:
    path, project, task_id, session_id, checkpoint_id = workspace
    bridge = Bridge()
    db = ProjectLocator.get_database_path(path)
    before = db.read_bytes()
    root = project["root"]
    assert request(bridge, "verify", root=root)["result"]["project_id"] == project["project_id"]
    overview = request(bridge, "overview", **project)["result"]
    assert overview["active_task"]["id"] == task_id
    assert overview["latest_session"]["id"] == session_id
    assert overview["checkpoint"]["id"] == checkpoint_id
    assert request(bridge, "tasks", **project)["result"]["items"][0]["session_count"] == 1
    detail = request(bridge, "task", task_id=task_id, **project)["result"]
    assert detail["decisions"]["items"] == ["Use SQLite"]
    first = request(bridge, "objective", task_id=task_id, **project)["result"]
    second = request(bridge, "objective", task_id=task_id, offset=first["next"], **project)[
        "result"
    ]
    assert (first["text"] + second["text"]) == OBJECTIVE[: len(first["text"] + second["text"])]
    assert first["next"] is not None
    sessions = request(bridge, "sessions", task_id=task_id, **project)["result"]["items"]
    assert sessions[0]["native_resume"] is True
    assert sessions[0]["checkpoint"]["id"] == checkpoint_id
    assert request(bridge, "checkpoints", **project)["result"]["items"][0]["id"] == checkpoint_id
    record = request(bridge, "checkpoint", record_id=checkpoint_id, **project)["result"]
    assert record["decisions"]["items"] == ["Use SQLite"]
    assert db.read_bytes() == before
    assert not (db.parent / "agent.lock").exists()


def test_wrong_missing_and_live_workspace(
    workspace: tuple[Path, dict[str, str], str, str, str], tmp_path_factory: pytest.TempPathFactory
) -> None:
    root, project, _, _, _ = workspace
    bridge = Bridge()
    wrong = tmp_path_factory.mktemp("wrong")
    assert request(bridge, "verify", root=str(wrong))["error"]["code"] == "not_initialized"
    shutil.copytree(root / ".cortexshift", wrong / ".cortexshift")
    assert request(bridge, "verify", root=str(wrong))["error"]["code"] == "identity_mismatch"
    assert (
        request(bridge, "overview", **(project | {"project_id": "wrong"}))["error"]["code"]
        == "identity_mismatch"
    )
    with FileWorkspaceLease(ProjectLocator.get_state_dir(root) / LOCK_FILE_NAME):
        assert request(bridge, "overview", **project)["result"]["workspace_busy"] is True
        TaskWorkspaceService().start_task("CLI update", "Visible in WAL", start_dir=root)
        assert (
            request(bridge, "overview", **project)["result"]["active_task"]["title"] == "CLI update"
        )


def test_contention_returns_retryable_error(
    workspace: tuple[Path, dict[str, str], str, str, str],
) -> None:
    root, project, _, _, _ = workspace
    connection = sqlite3.connect(ProjectLocator.get_database_path(root))
    try:
        connection.execute("PRAGMA locking_mode = EXCLUSIVE")
        connection.execute("BEGIN EXCLUSIVE")
        connection.execute("UPDATE projects SET name = name")
        error = request(Bridge(), "overview", **project)["error"]
        assert error["code"] == "state_busy"
    finally:
        connection.close()


def test_stdio_oversized_recovery_and_clean_exit() -> None:
    frame = b'{"version":1,"id":3,"op":"hello"}\n'
    completed = subprocess.run(
        [sys.executable, "-m", "cortexshift.desktop.bridge"],
        input=b"x" * (MAX_REQUEST_BYTES * 2) + b"\n" + frame + frame.replace(b"3", b"4"),
        capture_output=True,
        timeout=5,
        check=True,
    )
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [response["id"] for response in responses] == [None, 3, 4]
    assert responses[0]["error"]["code"] == "frame_too_large"
    assert completed.stderr == b""


def test_persisted_records_round_trip_over_stdio(
    workspace: tuple[Path, dict[str, str], str, str, str],
) -> None:
    _, identity, task_id, session_id, checkpoint_id = workspace
    frames = [
        {"op": "tasks"},
        {"op": "task", "task_id": task_id},
        {"op": "sessions", "task_id": task_id},
        {"op": "checkpoints", "task_id": task_id},
        {"op": "sessions", "task_id": "unrelated"},
    ]
    payload = "".join(
        json.dumps({"version": 1, "id": index, **identity, **frame}) + "\n"
        for index, frame in enumerate(frames, start=1)
    ).encode()
    result = subprocess.run(
        [sys.executable, "-I", "-m", "cortexshift.desktop.bridge"],
        input=payload,
        capture_output=True,
        timeout=8,
        check=True,
    )
    replies = [json.loads(line) for line in result.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, 3, 4, 5]
    assert replies[0]["result"]["items"][0]["id"] == task_id
    assert replies[1]["result"]["decisions"]["items"] == ["Use SQLite"]
    assert replies[2]["result"]["items"][0]["id"] == session_id
    assert replies[3]["result"]["items"][0]["id"] == checkpoint_id
    assert replies[4]["result"]["items"] == []
    assert result.stderr == b""


def test_streamed_creation_is_idempotent_and_checks_active_pointer(
    workspace: tuple[Path, dict[str, str], str, str, str],
) -> None:
    from io import BytesIO

    from cortexshift.desktop.bridge import serve

    _, project, previous, _, _ = workspace
    objective = ("  # Title 🧪\r\n\n" * 9000 + "  ").encode()
    params = {
        "version": 1,
        "id": 1,
        "op": "create_task",
        **project,
        "task_id": "task_0123456789abcdef0123456789abcdef",
        "title": "Desktop objective",
        "activate": True,
        "expected_active": previous,
        "objective_bytes": len(objective),
    }

    def create(**updates: Any) -> dict[str, Any]:
        source = BytesIO(json.dumps(params | updates).encode() + b"\n" + objective)
        sink = BytesIO()
        serve(source, sink)
        return dict(json.loads(sink.getvalue()))

    assert create(expected_active=None)["error"]["code"] == "active_task_changed"
    first = create()["result"]
    assert create()["result"] == first
    stored = request(Bridge(), "objective", **project, task_id=first["id"])
    assert stored["result"]["text"] == objective.decode()[:8192]
    assert request(Bridge(), "task", **project, task_id=previous)["result"]["id"] == previous
