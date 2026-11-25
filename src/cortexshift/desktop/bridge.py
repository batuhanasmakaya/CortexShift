"""Versioned JSON lines for CortexShift Desktop. Stdout is reserved for responses."""

import json
import sys
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from cortexshift import __version__
from cortexshift.application.desktop_control import (
    AgentAction,
    AgentPlan,
    DesktopControl,
    Identifier,
    UnsupportedDesktopProvider,
    provider_path,
    verified,
)
from cortexshift.application.workspace_reader import WorkspaceReader
from cortexshift.desktop.terminal import (
    DesktopActionBlocked,
    Jobs,
    run_job,
    terminal_available,
    unresolved_jobs,
)
from cortexshift.domain.errors import (
    ActiveTaskChangedError,
    ActiveTaskExistsError,
    CheckpointNotFoundError,
    CortexShiftError,
    DatabaseStateError,
    HandoffNotFoundError,
    NativeResumeError,
    NoActiveTaskError,
    NoSourceSessionError,
    ProjectConflictError,
    ProjectNotInitializedError,
    ProviderNotFoundError,
    SameProviderSwitchError,
    SchemaUpgradeRequiredError,
    SessionNotFoundError,
    SessionRecoveryError,
    SessionTaskMismatchError,
    StateBusyError,
    TaskCreationConflictError,
    TaskNotFoundError,
    UnsupportedSchemaVersionError,
    WorkspaceLockedError,
)

PROTOCOL = 1
MAX_REQUEST_BYTES = 32 * 1024
MAX_RESPONSE_BYTES = 512 * 1024
MAX_OBJECTIVE_BYTES = 1_000_000_000

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


class CreateRequest(TaskRequest):
    task_id: Annotated[str, Field(pattern=r"^task_[0-9a-f]{32}$")]
    title: Annotated[str, Field(min_length=1, max_length=1024)]
    activate: bool
    expected_active: Identifier | None
    objective_bytes: Annotated[int, Field(ge=1, le=MAX_OBJECTIVE_BYTES)]


class ActivateRequest(TaskRequest):
    expected_active: Identifier | None


type ManagedProvider = Literal["claude", "codex"]


class ManagedAction(AgentAction):
    provider: ManagedProvider | None = None


class ManagedPlan(AgentPlan):
    provider: ManagedProvider


class PlanRequest(WorkspaceRequest):
    action: ManagedAction


class SetupRequest(WorkspaceRequest):
    provider: ManagedProvider


class JobRequest(WorkspaceRequest):
    control_dir: Annotated[str, Field(min_length=1, max_length=8192, pattern=r"^[^\x00]+$")]


class SubmitRequest(JobRequest):
    operation_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    action: ManagedAction
    plan: ManagedPlan


class CancelRequest(JobRequest):
    action_id: Identifier


class RecoveryRequest(WorkspaceRequest):
    preview: bool


class UpdateGuardRequest(Request):
    control_dir: Annotated[str, Field(min_length=1, max_length=8192)]
    project_ids: Annotated[list[Identifier], Field(max_length=100)]


OPERATIONS: dict[str, type[Request]] = {
    "hello": Request,
    "verify": RootRequest,
    "overview": WorkspaceRequest,
    "tasks": PageRequest,
    "task": TaskRequest,
    "objective": ObjectiveRequest,
    "sessions": HistoryRequest,
    "session": RecordRequest,
    "checkpoints": HistoryRequest,
    "handoffs": HistoryRequest,
    "checkpoint": RecordRequest,
    "handoff": RecordRequest,
    "create_task": CreateRequest,
    "activate_task": ActivateRequest,
    "providers": WorkspaceRequest,
    "provider_setup": SetupRequest,
    "plan": PlanRequest,
    "submit": SubmitRequest,
    "action_status": JobRequest,
    "cancel_action": CancelRequest,
    "recover": RecoveryRequest,
    "update_guard": UpdateGuardRequest,
}


def failure(request_id: int | None, code: str, message: str) -> dict[str, Any]:
    return {"version": PROTOCOL, "id": request_id, "error": {"code": code, "message": message}}


class Bridge:
    def __init__(self) -> None:
        self._reader = WorkspaceReader()
        self._control = DesktopControl()

    def respond(self, raw: bytes, source: BinaryIO | None = None) -> dict[str, Any]:
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
            # Drain the bounded payload even when other header fields are invalid.
            objective = None
            if op == "create_task":
                size = frame.get("objective_bytes")
                if source is None or type(size) is not int or not 1 <= size <= MAX_OBJECTIVE_BYTES:
                    raise ValueError
                data = source.read(size)
                if len(data) != size:
                    raise ValueError
                objective = data.decode("utf-8")
            model = OPERATIONS[op].model_validate(frame)
            result = self._dispatch(model, objective)
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
        except (
            CheckpointNotFoundError,
            HandoffNotFoundError,
            TaskNotFoundError,
            SessionNotFoundError,
        ):
            return failure(
                request_id, "not_found", "This record is unavailable in the selected project."
            )
        except (ActiveTaskExistsError, ActiveTaskChangedError):
            return failure(
                request_id,
                "active_task_changed",
                "The active Task changed. Refresh and confirm your selection.",
            )
        except TaskCreationConflictError:
            return failure(
                request_id,
                "creation_conflict",
                "This creation ID already belongs to a different Task.",
            )
        except WorkspaceLockedError:
            return failure(
                request_id,
                "workspace_busy",
                "Workspace ownership blocks this action. Let the agent finish and refresh.",
            )
        except (
            DesktopActionBlocked,
            UnsupportedDesktopProvider,
            ProviderNotFoundError,
            NoSourceSessionError,
            NativeResumeError,
            NoActiveTaskError,
            SameProviderSwitchError,
            SessionTaskMismatchError,
            SessionRecoveryError,
        ) as error:
            return failure(request_id, "action_blocked", str(error))
        except (DatabaseStateError, OSError):
            return failure(
                request_id, "state_unavailable", "CortexShift could not read project state."
            )
        except CortexShiftError:
            return failure(
                request_id,
                "action_failed",
                "The action could not be prepared. Check the active Task and workspace.",
            )
        except Exception:
            # Tracebacks can contain canonical content or machine paths.
            print("CortexShift Desktop bridge encountered an internal error.", file=sys.stderr)
            return failure(request_id, "internal", "The bridge could not complete the request.")

    def _dispatch(self, request: Request, objective: str | None) -> dict[str, Any]:
        op = request.op
        if op == "hello":
            return {"protocol": PROTOCOL, "core_version": __version__}
        if isinstance(request, UpdateGuardRequest):
            return {"busy": unresolved_jobs(Path(request.control_dir), set(request.project_ids))}
        assert isinstance(request, RootRequest)
        root = Path(request.root)
        if op == "verify":
            return self._reader.verify(root)
        assert isinstance(request, WorkspaceRequest)
        project = request.project_id
        if op in {
            "create_task",
            "activate_task",
            "providers",
            "provider_setup",
            "plan",
            "submit",
            "action_status",
            "cancel_action",
            "recover",
        }:
            workspace = verified(root, project)
            match request:
                case CreateRequest():
                    assert objective is not None
                    return self._control.create(
                        workspace,
                        request.task_id,
                        request.title,
                        objective,
                        request.activate,
                        request.expected_active,
                    )
                case ActivateRequest():
                    return self._control.activate(
                        workspace, request.task_id, request.expected_active
                    )
                case SetupRequest():
                    return self._control.provider_setup(workspace, request.provider)
                case PlanRequest():
                    return self._control.plan(
                        workspace, request.action, provider_path(root)
                    ).model_dump()
                case JobRequest():
                    jobs = Jobs(Path(request.control_dir), project)
                    match request:
                        case SubmitRequest():
                            return jobs.submit(
                                workspace, request.action, request.plan, request.operation_id
                            )
                        case CancelRequest():
                            return jobs.cancel(request.action_id)
                        case _:
                            result = jobs.status()
                            receipt = result["action"]
                            if (
                                receipt
                                and receipt["phase"] == "running"
                                and receipt.get("session_id")
                            ):
                                receipt["objective_served"] = self._reader.session_objective_prefix(
                                    root, project, receipt["session_id"]
                                )
                            return result | {
                                "workspace_busy": self._reader.workspace_activity(workspace)
                            }
                case RecoveryRequest():
                    return self._control.recover(workspace, request.preview)
                case _:
                    return self._control.providers(workspace) | {"terminal": terminal_available()}
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
                query_record = {
                    "session": self._reader.session,
                    "checkpoint": self._reader.checkpoint,
                    "handoff": self._reader.handoff,
                }[op]
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
        response = bridge.respond(raw, source)
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


def main() -> int:
    args = sys.argv[1:]
    if not args:
        serve(sys.stdin.buffer, sys.stdout.buffer)
        return 0
    if len(args) == 2 and args[0] == "--terminal-job":
        return run_job(Path(args[1]))
    if getattr(sys, "frozen", False) and args == ["-m", "cortexshift", "mcp", "serve"]:
        # The managed adapters use this same fixed argv in frozen and Python installs.
        from cortexshift.mcp.context import resolve_mcp_context
        from cortexshift.mcp.server import run_mcp_server

        try:
            context, store = resolve_mcp_context()
            with store:
                run_mcp_server(context=context, store=store)
            return 0
        except Exception:
            print("CortexShift MCP could not start.", file=sys.stderr)
            return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
