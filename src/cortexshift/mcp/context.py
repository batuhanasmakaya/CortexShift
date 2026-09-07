"""Execution context resolution and validation for CortexShift MCP server."""

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from cortexshift.adapters.sqlite.store import SQLiteStateStore
from cortexshift.application.locator import ProjectLocator
from cortexshift.domain.errors import McpContextError
from cortexshift.domain.mcp_binding import (
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
    task_id: str | None = None
    session_id: str | None = None
    provider_id: ProviderId | None = None
    managed_session: bool = False
    read_only: bool = False

    def can_mutate(self) -> bool:
        """Return True if write operations are permitted in this context."""
        return self.managed_session and not self.read_only


def resolve_mcp_context(
    project_root_override: Path | None = None,
    store_override: SQLiteStateStore | None = None,
    env_override: dict[str, str] | None = None,
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

    # 1. Resolve project root
    explicit_root_str = env.get(ENV_PROJECT_ROOT)
    candidate_root: Path | None = None
    if project_root_override is not None:
        candidate_root = project_root_override.resolve()
    elif explicit_root_str:
        candidate_root = Path(explicit_root_str).resolve()
    else:
        candidate_root = ProjectLocator.find_project_root()

    if candidate_root is None or not ProjectLocator.is_initialized(candidate_root):
        loc = candidate_root or Path.cwd()
        raise McpContextError(f"No initialized CortexShift project found at or above '{loc}'.")

    project_root = candidate_root.resolve()

    # 2. Connect store and fetch project
    store = (
        store_override
        if store_override is not None
        else SQLiteStateStore(ProjectLocator.get_database_path(project_root))
    )

    project = store.get_default_project()
    if project is None:
        raise McpContextError(f"No project record found in database at {project_root}.")

    if Path(project.repo_path).resolve() != project_root:
        raise McpContextError(
            f"Project root mismatch: record specifies '{project.repo_path}', "
            f"but current root is '{project_root}'."
        )

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

        context = McpExecutionContext(
            project_root=project_root,
            project_id=project.id,
            task_id=session.task_id,
            session_id=session.id,
            provider_id=session.provider_id,
            managed_session=True,
            read_only=explicit_read_only,
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
            project_root=project_root,
            project_id=project.id,
            task_id=resolved_task_id,
            session_id=None,
            provider_id=None,
            managed_session=False,
            read_only=True,
        )

    return context, store
