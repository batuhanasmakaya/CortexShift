"""Execution context resolution and validation for CortexShift MCP server."""

import os
from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.mcp_invocation import require_active_invocation
from cortexshift.application.workspace import WorkspaceIdentity, open_workspace
from cortexshift.domain.errors import (
    CortexShiftError,
    McpContextError,
    McpReadOnlyError,
    ProjectNotInitializedError,
)
from cortexshift.domain.mcp_binding import (
    ENV_INVOCATION_TOKEN,
    ENV_MCP_READ_ONLY,
    ENV_PROJECT_ROOT,
    ENV_PROVIDER_ID,
    ENV_SESSION_ID,
    ENV_TASK_ID,
)
from cortexshift.domain.provider import ProviderId

__all__ = [
    "ENV_MCP_READ_ONLY",
    "ENV_PROJECT_ROOT",
    "ENV_PROVIDER_ID",
    "ENV_SESSION_ID",
    "ENV_TASK_ID",
    "McpExecutionContext",
    "resolve_mcp_context",
]


class McpExecutionContext(BaseModel):
    """Canonical execution context binding the MCP server to local project state."""

    model_config = ConfigDict(frozen=True)

    project_root: Path
    project_id: str
    workspace: WorkspaceIdentity | None = Field(default=None, exclude=True, repr=False)
    task_id: str | None = None
    session_id: str | None = None
    provider_id: ProviderId | None = None
    managed_session: bool = False
    read_only: bool = False
    invocation_token: SecretStr | None = Field(default=None, exclude=True, repr=False)

    def can_mutate(self) -> bool:
        """Return whether this context exposes write tools at startup."""
        return self.managed_session and not self.read_only


def resolve_mcp_context(
    project_root_override: Path | None = None,
    store_override: SQLiteStateStore | None = None,
    env_override: dict[str, str] | None = None,
    *,
    auto_migrate: bool = True,
) -> tuple[McpExecutionContext, SQLiteStateStore]:
    """Resolve and validate the canonical McpExecutionContext.

    Validates:
    - Project initialization and root resolution
    - Session existence, task ownership, and provider identity (if session_id provided)
    - Bound task existence and project membership
    - Read-only enforcement for unmanaged invocations or explicit read-only flag

    Returns:
        A tuple of (McpExecutionContext, initialized SQLiteStateStore).

    Raises:
        McpContextError: If the context violates invariants or SQLite state is invalid.
    """
    env = os.environ if env_override is None else env_override

    explicit_root = project_root_override or env.get(ENV_PROJECT_ROOT) or None
    try:
        workspace, store = open_workspace(
            explicit_root,
            exact_root=explicit_root is not None,
            auto_migrate=auto_migrate,
            store_override=store_override,
        )
    except ProjectNotInitializedError as exc:
        raise McpContextError(
            f"No initialized CortexShift project found at or above '{explicit_root or Path.cwd()}'."
        ) from exc
    except (CortexShiftError, OSError, RuntimeError) as exc:
        raise McpContextError(str(exc)) from exc

    try:
        return _bind_context(workspace, store, env), store
    except BaseException:
        if store_override is None:
            store.close()
        raise


def _bind_context(
    workspace: WorkspaceIdentity,
    store: SQLiteStateStore,
    env: Mapping[str, str],
) -> McpExecutionContext:
    project = workspace.project

    # 3. Read-only override
    explicit_read_only = env.get(ENV_MCP_READ_ONLY, "0").strip().lower() in ("1", "true", "yes")

    session_id = env.get(ENV_SESSION_ID)
    env_task_id = env.get(ENV_TASK_ID)
    env_provider_id = env.get(ENV_PROVIDER_ID)

    if session_id:
        # Managed invocation: must be strictly validated
        session = store.get_session(session_id)
        if session is None:
            raise McpContextError(f"Session '{session_id}' not found in project '{project.id}'.")

        if env_task_id and session.task_id != env_task_id:
            raise McpContextError(
                f"Session '{session_id}' task '{session.task_id}' does not match "
                f"environment task '{env_task_id}'."
            )

        if env_provider_id and str(session.provider_id) != env_provider_id:
            raise McpContextError(
                f"Session '{session_id}' provider '{session.provider_id}' does not match "
                f"environment provider '{env_provider_id}'."
            )

        task = store.get_task(session.task_id)
        if task is None:
            raise McpContextError(
                f"Task '{session.task_id}' bound to session '{session_id}' not found."
            )

        if task.project_id != project.id:
            raise McpContextError(f"Task '{task.id}' does not belong to project '{project.id}'.")

        token = env.get(ENV_INVOCATION_TOKEN)
        invocation_token = None
        if not explicit_read_only and env_task_id and env_provider_id and token:
            try:
                # Serialize lock probes too. On Windows a probe briefly takes an
                # exclusive lock and must never be mistaken for the launcher.
                with store.write_transaction():
                    require_active_invocation(
                        workspace,
                        store,
                        session_id=session.id,
                        task_id=env_task_id,
                        provider_id=session.provider_id,
                        token=SecretStr(token),
                    )
            except McpReadOnlyError:
                pass
            else:
                invocation_token = SecretStr(token)

        context = McpExecutionContext(
            project_root=workspace.root,
            workspace=workspace,
            project_id=project.id,
            task_id=session.task_id,
            session_id=session.id,
            provider_id=session.provider_id,
            managed_session=True,
            read_only=invocation_token is None,
            invocation_token=invocation_token,
        )
    else:
        # Unmanaged invocation: strictly read-only
        candidate_task_id = env_task_id or store.get_active_task_id(project.id)
        resolved_task_id: str | None = None
        if candidate_task_id:
            task = store.get_task(candidate_task_id)
            if task and task.project_id == project.id:
                resolved_task_id = task.id

        context = McpExecutionContext(
            project_root=workspace.root,
            workspace=workspace,
            project_id=project.id,
            task_id=resolved_task_id,
            session_id=None,
            provider_id=None,
            managed_session=False,
            read_only=True,
        )

    return context
