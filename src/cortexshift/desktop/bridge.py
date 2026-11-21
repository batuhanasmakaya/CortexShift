"""Versioned JSON lines for CortexShift Desktop. Stdout is reserved for responses."""

import json
import sys
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cortexshift import __version__
from cortexshift.application.workspace_reader import WorkspaceReader
from cortexshift.domain.errors import (
    CheckpointNotFoundError,
    DatabaseStateError,
    HandoffNotFoundError,
    ProjectConflictError,
    ProjectNotInitializedError,
    SchemaUpgradeRequiredError,
    StateBusyError,
    TaskNotFoundError,
    UnsupportedSchemaVersionError,
)

PROTOCOL = 1
MAX_REQUEST_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 512 * 1024

type Identifier = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.-]+$")]
type Offset = Annotated[int, Field(ge=0, le=2_147_483_647)]


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1]
    id: Annotated[int, Field(ge=1, le=2**53 - 1)]
    op: str


class RootRequest(Request):
    root: Annotated[str, Field(min_length=1, max_length=8192, pattern=r"^[^\x00]+$")]


class WorkspaceRequest(RootRequest):
    project_id: Identifier


class PageRequest(WorkspaceRequest):
    offset: Offset = 0


class HistoryRequest(PageRequest):
    task_id: Identifier | None = None


class TaskRequest(WorkspaceRequest):
    task_id: Identifier


class ObjectiveRequest(TaskRequest):
    offset: Offset = 0


class RecordRequest(WorkspaceRequest):
    record_id: Identifier


OPERATIONS: dict[str, type[Request]] = {
    "hello": Request,
    "verify": RootRequest,
    "overview": WorkspaceRequest,
    "tasks": PageRequest,
    "task": TaskRequest,
    "objective": ObjectiveRequest,
    "sessions": HistoryRequest,
    "checkpoints": HistoryRequest,
    "handoffs": HistoryRequest,
    "checkpoint": RecordRequest,
    "handoff": RecordRequest,
}


def failure(request_id: int | None, code: str, message: str) -> dict[str, Any]:
    return {"version": PROTOCOL, "id": request_id, "error": {"code": code, "message": message}}


class Bridge:
    def __init__(self) -> None:
        self._reader = WorkspaceReader()

    def respond(self, raw: bytes) -> dict[str, Any]:
        request_id: int | None = None
        try:
            if len(raw) > MAX_REQUEST_BYTES:
                return failure(None, "frame_too_large", "The bridge request exceeded 32 KiB.")
            frame = json.loads(raw)
            if not isinstance(frame, dict):
                raise ValueError
            identifier = frame.get("id")
            if type(identifier) is int and 1 <= identifier <= 2**53 - 1:
                request_id = identifier
            version = frame.get("version")
            if type(version) is not int or version != PROTOCOL:
                return failure(request_id, "protocol_mismatch", "Bridge protocol 1 is required.")
            op = frame.get("op")
            if not isinstance(op, str) or op not in OPERATIONS:
                return failure(request_id, "unknown_operation", "That bridge operation is unknown.")
            model = OPERATIONS[op].model_validate(frame)
            result = self._dispatch(model)
            return {"version": PROTOCOL, "id": model.id, "result": result}
        except (ValidationError, ValueError, RecursionError):
            return failure(request_id, "invalid_request", "The bridge request is invalid.")
        except ProjectNotInitializedError:
            return failure(
                request_id,
                "not_initialized",
                "Choose an initialized project root, or run cortexshift init in that folder first.",
            )
        except ProjectConflictError:
            return failure(
                request_id,
                "identity_mismatch",
                "Project identity changed or belongs to another folder. Choose its original root.",
            )
        except SchemaUpgradeRequiredError:
            return failure(
                request_id,
                "schema_upgrade_required",
                "Open the workspace with the matching CortexShift CLI and run cortexshift init.",
            )
        except UnsupportedSchemaVersionError:
            return failure(
                request_id, "schema_too_new", "Update CortexShift Desktop to open this project."
            )
        except StateBusyError:
            return failure(request_id, "state_busy", "Project state is busy. Refresh to try again.")
        except (CheckpointNotFoundError, HandoffNotFoundError, TaskNotFoundError):
            return failure(
                request_id, "not_found", "This record is unavailable in the selected project."
            )
        except (DatabaseStateError, OSError):
            return failure(
                request_id, "state_unavailable", "CortexShift could not read project state."
            )
        except Exception:
            # Tracebacks can contain canonical content or machine paths.
            print("CortexShift Desktop bridge encountered an internal error.", file=sys.stderr)
            return failure(request_id, "internal", "The bridge could not complete the request.")

    def _dispatch(self, request: Request) -> dict[str, Any]:
        op = request.op
        if op == "hello":
            return {"protocol": PROTOCOL, "core_version": __version__}
        assert isinstance(request, RootRequest)
        root = Path(request.root)
        if op == "verify":
            return self._reader.verify(root)
        assert isinstance(request, WorkspaceRequest)
        project = request.project_id
        match request:
            case HistoryRequest(task_id=task, offset=offset):
                query = {
                    "sessions": self._reader.sessions,
                    "checkpoints": self._reader.checkpoints,
                    "handoffs": self._reader.handoffs,
                }[op]
                return query(root, project, task, offset)
            case PageRequest(offset=offset):
                return self._reader.tasks(root, project, offset)
            case ObjectiveRequest(task_id=task, offset=offset):
                return self._reader.objective(root, project, task, offset)
            case TaskRequest(task_id=task):
                return self._reader.task(root, project, task)
            case RecordRequest(record_id=record_id):
                query_record = (
                    self._reader.checkpoint if op == "checkpoint" else self._reader.handoff
                )
                return query_record(root, project, record_id)
            case _:
                return self._reader.overview(root, project)


def serve(source: BinaryIO, sink: BinaryIO) -> None:
    bridge = Bridge()
    while line := source.readline(MAX_REQUEST_BYTES + 2):
        raw = line.removesuffix(b"\n")
        if len(raw) > MAX_REQUEST_BYTES and not line.endswith(b"\n"):
            while chunk := source.readline(MAX_REQUEST_BYTES):
                if chunk.endswith(b"\n"):
                    break
        response = bridge.respond(raw)
        encoded = json.dumps(response, ensure_ascii=True, separators=(",", ":")).encode()
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = json.dumps(
                failure(
                    response["id"], "response_too_large", "The record exceeds the display limit."
                )
            ).encode()
        try:
            sink.write(encoded + b"\n")
            sink.flush()
        except BrokenPipeError:
            return


if __name__ == "__main__":
    serve(sys.stdin.buffer, sys.stdout.buffer)
