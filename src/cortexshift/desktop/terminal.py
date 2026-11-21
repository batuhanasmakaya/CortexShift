"""One-shot native terminal jobs with bounded, durable lifecycle receipts."""

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from threading import Thread
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from cortexshift.adapters.advisory_lock import is_locked, release_lock, try_lock
from cortexshift.application.desktop_control import (
    AgentAction,
    AgentPlan,
    DesktopControl,
    provider_path,
    verified,
)
from cortexshift.application.workspace import WorkspaceIdentity
from cortexshift.domain.errors import CortexShiftError
from cortexshift.domain.session import SessionStatus

TERMINAL = Path("/System/Applications/Utilities/Terminal.app")
UNFINISHED = frozenset({"pending", "starting", "context_prepared", "running"})


class DesktopActionBlocked(CortexShiftError):
    pass


class Ticket(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    root: str
    project_id: str
    action: AgentAction
    plan: AgentPlan
    path: str


def terminal_available() -> bool:
    return (sys.platform == "darwin" and TERMINAL.is_dir()) or sys.platform == "win32"


def _atomic_write(path: Path, body: bytes) -> None:
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def _state(directory: Path, phase: str, **data: Any) -> dict[str, Any]:
    state = {"id": directory.name, "phase": phase, **data}
    _atomic_write(directory / "status.json", json.dumps(state).encode())
    return state


def _read_state(directory: Path) -> dict[str, Any]:
    value = _read_state_without_lock(directory)
    if value["phase"] in UNFINISHED and not is_locked(directory / "worker.lock"):
        # A worker can finalize while we probe. Read the receipt again before classifying.
        value = _read_state_without_lock(directory)
        if (
            value["phase"] in UNFINISHED
            and not (directory / "request.json").exists()
            and not is_locked(directory / "worker.lock")
        ):
            return {**value, "phase": "unknown"}
    return value


class Jobs:
    def __init__(self, directory: Path, project_id: str) -> None:
        if not directory.is_absolute():
            raise ValueError
        self._root = directory / hashlib.sha256(project_id.encode()).hexdigest()

    def _current(self) -> Path | None:
        try:
            identifier = (self._root / "current").read_text(encoding="ascii")
        except FileNotFoundError:
            return None
        if not re.fullmatch(r"job-[0-9a-f]{32}", identifier):
            raise ValueError
        return self._root / identifier

    def status(self) -> dict[str, Any]:
        directory = self._current()
        return {"action": _read_state(directory) if directory else None}

    def submit(
        self,
        workspace: WorkspaceIdentity,
        action: AgentAction,
        plan: AgentPlan,
        operation_id: str,
    ) -> dict[str, Any]:
        if not terminal_available():
            raise DesktopActionBlocked("Native terminal launch is unavailable on this platform.")
        self._root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not re.fullmatch(r"[0-9a-f]{32}", operation_id):
            raise ValueError
        directory = self._root / f"job-{operation_id}"
        previous = self._current()
        if previous == directory:
            return {"action": _read_state(directory)}
        if previous and _read_state(previous)["phase"] in UNFINISHED:
            raise DesktopActionBlocked("A Desktop invocation is already pending or running.")
        path = provider_path(workspace.root)
        DesktopControl.check_action(workspace, action)
        # Publishing current is the commit point. A partial unpublished ticket cannot launch.
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError as error:
            raise DesktopActionBlocked("This action ID has already been used.") from error
        ticket = Ticket(
            root=str(workspace.root),
            project_id=workspace.project.id,
            action=action,
            plan=plan,
            path=path,
        )
        _atomic_write(directory / "request.json", ticket.model_dump_json().encode())
        state = _state(directory, "pending", mode=plan.mode, provider=plan.provider)
        _atomic_write(self._root / "current", directory.name.encode())
        if previous:
            # Completed/cancelled tickets cannot be run again. Clean only files we own.
            for name in ("launch.command", "status.json", "worker.lock", "request.json"):
                with suppress(OSError):
                    (previous / name).unlink(missing_ok=True)
        try:
            _open_terminal(directory)
        except (OSError, subprocess.SubprocessError):
            try:
                return self.cancel(directory.name, failed=True)
            except DesktopActionBlocked:
                return self.status()
        return {"action": state}

    def cancel(self, identifier: str, *, failed: bool = False) -> dict[str, Any]:
        directory = self._current()
        if directory is None or directory.name != identifier:
            raise DesktopActionBlocked("The Desktop action changed. Refresh its status.")
        fd = try_lock(directory / "worker.lock", create=True)
        if fd is None:
            raise DesktopActionBlocked("The terminal worker already owns this action.")
        try:
            current = _read_state_without_lock(directory)
            if current["phase"] != "pending" or not (directory / "request.json").is_file():
                raise DesktopActionBlocked("The terminal already claimed this action.")
            (directory / "request.json").unlink()
            return {
                "action": _state(
                    directory,
                    "failed" if failed else "cancelled",
                    mode=current["mode"],
                    provider=current["provider"],
                    message="Could not open the native terminal." if failed else None,
                )
            }
        finally:
            release_lock(fd)


def _read_state_without_lock(directory: Path) -> dict[str, Any]:
    return dict(json.loads((directory / "status.json").read_bytes()))


def _worker_argv(directory: Path) -> list[str]:
    argv = [sys.executable]
    if not getattr(sys, "frozen", False):
        argv += ["-I", "-m", "cortexshift.desktop.bridge"]
    return [*argv, "--terminal-job", str(directory)]


def _open_terminal(directory: Path) -> None:
    argv = _worker_argv(directory)
    if sys.platform == "darwin":
        wrapper = directory / "launch.command"
        # Only the app's binary and private ticket path are quoted into this fixed wrapper.
        if getattr(sys, "frozen", False):
            argv = ["/usr/bin/env", "PYINSTALLER_RESET_ENVIRONMENT=1", *argv]
        _atomic_write(wrapper, ("#!/bin/sh\nexec " + shlex.join(argv) + "\n").encode())
        wrapper.chmod(0o700)
        argv = ["/usr/bin/open", "-a", str(TERMINAL), str(wrapper)]
        subprocess.run(
            argv,
            cwd=directory,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=True,
        )
    elif sys.platform == "win32":
        child = subprocess.Popen(
            argv,
            cwd=directory,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
            env=os.environ | {"PYINSTALLER_RESET_ENVIRONMENT": "1"},
        )
        # Reap the immediate child without signalling the worker.
        Thread(target=child.wait, daemon=True).start()
    else:
        raise OSError("Native terminal launch is unavailable.")


def run_job(directory: Path) -> int:
    if not directory.is_absolute() or not directory.is_dir():
        return 1
    fd = try_lock(directory / "worker.lock", create=True)
    if fd is None:
        return 1
    try:
        request = directory / "request.json"
        if not request.is_file():
            return 1
        ticket = Ticket.model_validate_json(request.read_bytes())
        request.unlink()  # Consume before invoking a service. Reopening a window cannot replay it.
        session_id: str | None = None
        evidence: dict[str, Any] = {
            "provider": ticket.plan.provider,
            "mode": ticket.plan.mode,
            "spawned": False,
        }
        _state(directory, "starting", **evidence)

        def prepared(identifier: str, phase: str) -> None:
            nonlocal session_id
            session_id = identifier
            if phase == "running":
                evidence["spawned"] = True
            with suppress(OSError):
                _state(directory, phase, session_id=identifier, **evidence)

        try:
            workspace = verified(Path(ticket.root), ticket.project_id)
            os.environ["PATH"] = ticket.path
            session = DesktopControl().execute(
                workspace, ticket.action, ticket.plan, ticket.path, prepared
            )
            _state(directory, session.status.value, session_id=session.id, **evidence)
            return 0 if session.status == SessionStatus.COMPLETED else 1
        except (Exception, KeyboardInterrupt) as error:
            message = action_error(error)
            _state(directory, "failed", session_id=session_id, message=message, **evidence)
            print(message, file=sys.stderr)
            return 1
    except (OSError, ValidationError):
        return 1
    finally:
        release_lock(fd)


def action_error(error: BaseException) -> str:
    from cortexshift.domain.errors import (
        ActiveTaskChangedError,
        HandoffDeliveryError,
        NativeResumeError,
        ProjectConflictError,
        ProviderNotFoundError,
        SessionRecoveryError,
        TaskStartupError,
        TerminalRequiredError,
        WorkspaceLockedError,
    )

    if isinstance(error, (WorkspaceLockedError, SessionRecoveryError)):
        return "Workspace ownership blocks this action. Let the agent finish or review recovery."
    if isinstance(error, ProjectConflictError):
        return "The workspace identity changed. Reopen the project to verify it."
    if isinstance(error, ProviderNotFoundError):
        return "The provider executable is missing from the Desktop search path."
    if isinstance(error, (ActiveTaskChangedError, NativeResumeError)):
        return "The Task or Session selection changed. Review the action before retrying."
    if isinstance(error, HandoffDeliveryError):
        return "Handoff preparation or delivery failed. Review the Session and handoff records."
    if isinstance(error, (TaskStartupError, TerminalRequiredError)):
        return (
            "Provider startup failed. The active Task is saved. "
            "Check the native terminal and provider setup."
        )
    return (
        "The terminal action could not finish. Review workspace activity and the Session history."
    )
